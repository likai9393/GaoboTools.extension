# -*- coding: utf-8 -*-
__title__ = 'LINK_RVT'
__doc__ = '支持一键批量选择 RVT 文件，并提供包含项目基点在内的全版本精准对齐。'
__author__ = 'Gaobo' 

import os
from pyrevit import forms
from Autodesk.Revit.DB import *

# 获取当前文档上下文中
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

def get_all_rvt_links():
    """获取当前文件中所有的 RVT 链接类型，用于防重校验"""
    rvt_types = FilteredElementCollector(doc).OfClass(RevitLinkType).ToElements()
    return [t for t in rvt_types if not t.IsNestedLink]

def get_pbp_position(target_doc):
    """获取目标文档的项目基点(Project Base Point)坐标"""
    pbp_filter = ElementCategoryFilter(BuiltInCategory.OST_ProjectBasePoint)
    pbp_elem = FilteredElementCollector(target_doc).WherePasses(pbp_filter).FirstElement()
    if pbp_elem:
        ew = pbp_elem.get_Parameter(BuiltInParameter.BASEPOINT_EASTWEST_PARAM).AsDouble()
        ns = pbp_elem.get_Parameter(BuiltInParameter.BASEPOINT_NORTHSOUTH_PARAM).AsDouble()
        ele = pbp_elem.get_Parameter(BuiltInParameter.BASEPOINT_ELEVATION_PARAM).AsDouble()
        return XYZ(ew, ns, ele)
    return XYZ.Zero

def get_placement_enum(enum_name, string_val):
    """动态获取对齐枚举对象，兼容共享坐标"""
    import System
    enum_type = None
    for name in [enum_name, 'LinkPlacement', 'RevitLinkPlacement', 'RevitLinkPlacementReason']:
        if name in globals():
            enum_type = globals()[name]
            break
    if enum_type:
        try: return System.Enum.Parse(enum_type, string_val)
        except: pass
    return None

# ==================== 主程序入口 ====================

# 1. 运行即直接弹出多选文件框，省去中间多余的菜单确认
files = forms.pick_file(file_ext='rvt', multi_file=True, title="选择要批量链接的 RVT 文件")

if files:
    # 2. 选择对齐定位方式
    pos_options = [
        "自动 - 原点到原点", 
        "自动 - 通过共享坐标", 
        "自动 - 项目基点对齐", 
        "手动 - 中心"
    ]
    selected_pos = forms.SelectFromList.show(pos_options, title="选择对齐定位方式", button_name="应用此对齐")
    
    if selected_pos:
        existing_links = get_all_rvt_links()
        
        with Transaction(doc, "批量添加RVT链接") as t:
            t.Start()
            for f_path in files:
                try:
                    # 统一路径格式
                    filepath = ModelPathUtils.ConvertUserVisiblePathToModelPath(f_path)
                    filename = os.path.basename(f_path).lower()
                    
                    # 3. 智能查重：防范 "already contains a linked model" 冲突报错
                    matched_type = None
                    for el in existing_links:
                        try:
                            ext_path = el.GetExternalFileReference().GetAbsolutePath()
                            ext_string_path = ModelPathUtils.ConvertModelPathToUserVisiblePath(ext_path)
                            if os.path.basename(ext_string_path).lower() == filename:
                                matched_type = el
                                break
                        except: pass
                    
                    if matched_type is None:
                        options = RevitLinkOptions(False)
                        result = RevitLinkType.Create(doc, filepath, options)
                        link_type_id = result.ElementId
                    else:
                        link_type_id = matched_type.Id
                        if matched_type.GetLinkedFileStatus() == LinkedFileStatus.Unloaded:
                            matched_type.Reload()
                    
                    # ================= 4. 精准对齐与位移控制 =================
                    if "原点到原点" in selected_pos:
                        RevitLinkInstance.Create(doc, link_type_id)
                        
                    elif "共享坐标" in selected_pos:
                        p_enum = get_placement_enum('RevitLinkPlacementReason', 'Shared') or \
                                 get_placement_enum('LinkPlacement', 'SharedPositions') or \
                                 get_placement_enum('RevitLinkPlacement', 'SharedPositions')
                        if p_enum is not None:
                            RevitLinkInstance.Create(doc, link_type_id, p_enum)
                        else:
                            RevitLinkInstance.Create(doc, link_type_id)
                        
                    elif "项目基点" in selected_pos:
                        # 核心兼容算法：原点定位加载 + PBP 向量差位移
                        link_instance = RevitLinkInstance.Create(doc, link_type_id)
                        
                        main_pbp = get_pbp_position(doc)
                        link_doc = link_instance.GetLinkDocument()
                        link_pbp = XYZ.Zero
                        if link_doc:
                            link_pbp = get_pbp_position(link_doc)
                        
                        # 向量差 = 主模型基点 - 子模型基点
                        move_vector = main_pbp - link_pbp
                        
                        if not move_vector.IsZeroLength():
                            ElementTransformUtils.MoveElement(doc, link_instance.Id, move_vector)
                        
                    elif "手动" in selected_pos:
                        RevitLinkInstance.Create(doc, link_type_id)
                        print("提示: [{}] 已按原点放入，请手动调整位置。".format(f_path))
                        
                except Exception as e:
                    forms.alert("链接失败: {}\n错误: {}".format(f_path, e))
            t.Commit()
        
        # 5. 视图刷新，确保导入后能即时显形
        try: uidoc.RefreshActiveView()
        except: pass
        
        forms.alert("批量添加完成！", title="成功")