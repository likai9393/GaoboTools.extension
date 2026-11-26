# -*- coding: utf-8 -*-
import datetime
from pyrevit import DB, revit

# ----------------- 配置区域 -----------------
TARGET_FAMILY_NAME = "AA-Tag_Annocation_Date time" 
TARGET_PARAM_NAME = "Date time" 
TIME_FORMAT = "%d/%m/%y"
# --------------------------------------------

def update_annotation_time():
    try:
        doc = __eventargs__.Document
    except NameError:
        doc = revit.doc

    if not doc or doc.IsFamilyDocument:
        return

    # 1. 收集项目里所有的通用注释族
    collector = DB.FilteredElementCollector(doc)\
                  .OfCategory(DB.BuiltInCategory.OST_GenericAnnotation)\
                  .WhereElementIsNotElementType()

    # 2. 匹配目标族实例
    target_instances = [
        elem for elem in collector 
        if elem.Symbol and elem.Symbol.Family.Name == TARGET_FAMILY_NAME
    ]

    if not target_instances:
        return

    # 3. 生成当前日期字符串
    date_str = datetime.datetime.now().strftime(TIME_FORMAT)

    # 4. 修改参数并提交
    t = DB.Transaction(doc, "自动更新保存日期")
    try:
        t.Start()
        for inst in target_instances:
            param = inst.LookupParameter(TARGET_PARAM_NAME)
            if param and not param.IsReadOnly:
                param.Set(date_str)
        t.Commit()
        
        # 强制刷新界面
        if revit.uidoc and revit.uidoc.ActiveView:
            revit.uidoc.RefreshActiveView()
            
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()

# 执行
update_annotation_time()