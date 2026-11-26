# -*- coding: utf-8 -*-
__title__ = 'UpdateDiagonalCoords'
__doc__ = ("抓取 Electrical Fixtures 中当前可见的 Diagonal 子类别线（受互斥的 AA/BB 参数控制显隐），\n"
           "结合航站楼轮廓线及其距离，精准分配 C1(近端) 与 C2(远端) 及 DWG Z 投影中点。")
__author__ = 'Gaobo'

__persistentengine__ = True

import math
import os
import json
import Autodesk.Revit.DB as DB
import Autodesk.Revit.UI as UI
from pyrevit import script
from pyrevit.forms import WPFWindow
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

FEET_TO_METER = 0.3048

CATEGORIES = [
    DB.BuiltInCategory.OST_ElectricalFixtures
]

TARGET_LINE_SUBCATEGORY = "Diagonal"

PARAM_COORDI_1 = "coordi 1"
PARAM_COORDI_2 = "coordi 2"
PARAM_COORDI_C = "coordi c"

CACHE_FILE = os.path.join(os.path.dirname(__file__), "dwg_cache.json")
OUTLINE_CACHE_FILE = os.path.join(os.path.dirname(__file__), "outline_cache.json")

output = script.get_output()


# =========================================================================
# 单位 / 坐标转换
# =========================================================================

def to_meters(feet_val):
    return feet_val * FEET_TO_METER


def get_project_base_point_info(doc):
    collector = DB.FilteredElementCollector(doc).OfCategory(DB.BuiltInCategory.OST_ProjectBasePoint)
    pbp = collector.FirstElement()

    if not pbp:
        return 0.0, 0.0, 0.0

    def get_p_val_by_bip(bip):
        try:
            p = pbp.get_Parameter(bip)
            if p:
                return p.AsDouble()
        except Exception:
            pass
        return None

    def get_p_val_by_names(elem, possible_names):
        for name in possible_names:
            p = elem.LookupParameter(name)
            if p:
                return p.AsDouble()
        return 0.0

    east_ft = get_p_val_by_bip(DB.BuiltInParameter.BASEPOINT_EASTWEST_PARAM)
    if east_ft is None:
        east_ft = get_p_val_by_names(pbp, ["East/West", "东西", "東西"])

    north_ft = get_p_val_by_bip(DB.BuiltInParameter.BASEPOINT_NORTHSOUTH_PARAM)
    if north_ft is None:
        north_ft = get_p_val_by_names(pbp, ["North/South", "南北"])

    angle_rad = 0.0
    try:
        p_angle = pbp.get_Parameter(DB.BuiltInParameter.BASEPOINT_ANGLETONORTH_PARAM)
        if p_angle:
            angle_rad = p_angle.AsDouble()
    except Exception:
        pass

    if angle_rad == 0.0:
        angle_rad = get_p_val_by_names(pbp, ["Angle to True North", "正北角度", "到真短的角度", "旋轉至正北的角度"])

    base_e = east_ft * FEET_TO_METER
    base_n = north_ft * FEET_TO_METER

    return base_e, base_n, angle_rad


def convert_internal_to_survey_2d(pt_internal_ft, base_e, base_n, angle_rad):
    x_in = pt_internal_ft.X * FEET_TO_METER
    y_in = pt_internal_ft.Y * FEET_TO_METER

    effective_angle = -angle_rad
    cos_a = math.cos(effective_angle)
    sin_a = math.sin(effective_angle)

    x_rot = x_in * cos_a - y_in * sin_a
    y_rot = x_in * sin_a + y_in * cos_a

    easting = x_rot + base_e
    northing = y_rot + base_n

    str_coord = "{:.3f},{:.3f}".format(easting, northing)
    return str_coord, (easting, northing)


def convert_internal_to_survey_3d(pt_internal_ft, z_meters, base_e, base_n, angle_rad):
    _, (easting, northing) = convert_internal_to_survey_2d(pt_internal_ft, base_e, base_n, angle_rad)
    str_coord = "{:.3f},{:.3f},{:.3f}".format(easting, northing, z_meters)
    return str_coord, (easting, northing, z_meters)


# =========================================================================
# 轮廓线几何解析与判断逻辑（带调试打印）
# =========================================================================

def get_outline_curves(doc, outline_uids):
    curves = []
    for uid in outline_uids:
        el = doc.GetElement(uid)
        if el and hasattr(el, "GeometryCurve"):
            curves.append(el.GeometryCurve)
        elif el and isinstance(el, DB.DetailCurve):
            curves.append(el.GeometryCurve)
    return curves


def determine_c1_c2_by_outline(pt_a, pt_b, outline_curves):
    """
    遍历所有航站楼轮廓线段，精准计算端点 A 和端点 B 到轮廓的最短距离。
    距离轮廓更近的端点为 C1（近端），更远的为 C2（远端）。
    """
    if not outline_curves:
        print("  [警告] 未检测到有效的轮廓线缓存！降级使用 Y 坐标判定。")
        return (pt_a, pt_b) if pt_a.Y >= pt_b.Y else (pt_b, pt_a)

    min_dist_a = float('inf')
    min_dist_b = float('inf')

    for curve in outline_curves:
        proj_a = curve.Project(pt_a)
        if proj_a and proj_a.Distance < min_dist_a:
            min_dist_a = proj_a.Distance

        proj_b = curve.Project(pt_b)
        if proj_b and proj_b.Distance < min_dist_b:
            min_dist_b = proj_b.Distance

    print("  - [判定调试] 端点A到轮廓最近距离: {:.3f} 英尺 | 端点B到轮廓最近距离: {:.3f} 英尺".format(min_dist_a, min_dist_b))

    # 严格按照“距离更小的（更近的）赋给 C1”
    if min_dist_a <= min_dist_b:
        return pt_a, pt_b  # A 更近 -> C1=A, C2=B
    else:
        return pt_b, pt_a  # B 更近 -> C1=B, C2=A


# =========================================================================
# 几何提取
# =========================================================================

def collect_visible_diagonal_lines(elem, doc, view):
    opt = DB.Options()
    opt.ComputeReferences = True
    opt.View = view

    geom_elem = elem.get_Geometry(opt)
    lines = []
    found_subcategories = set()

    if not geom_elem:
        return lines, found_subcategories

    def _walk(geo):
        if not geo:
            return
        for g in geo:
            if isinstance(g, DB.GeometryInstance):
                _walk(g.GetInstanceGeometry())
            elif isinstance(g, DB.Line):
                gs = doc.GetElement(g.GraphicsStyleId)
                if gs and gs.GraphicsStyleCategory:
                    cat_name = gs.GraphicsStyleCategory.Name
                    found_subcategories.add(cat_name)
                    if cat_name == TARGET_LINE_SUBCATEGORY:
                        lines.append(g)

    _walk(geom_elem)
    return lines, list(found_subcategories)


# =========================================================================
# DWG 网格与高程计算
# =========================================================================

def extract_meshes_from_dwg(dwg_instance):
    meshes = []
    options = DB.Options()
    geom_elem = dwg_instance.get_Geometry(options)
    if not geom_elem:
        return meshes

    for obj in geom_elem:
        if isinstance(obj, DB.GeometryInstance):
            symbol_geom = obj.GetInstanceGeometry()
            for sub_obj in symbol_geom:
                if isinstance(sub_obj, DB.Mesh):
                    meshes.append(sub_obj)
                elif hasattr(sub_obj, "GetMesh") and sub_obj.GetMesh():
                    meshes.append(sub_obj.GetMesh())
    return meshes


def ray_cast_z_to_mesh(pt, meshes):
    px, py = pt.X, pt.Y
    best_z = None

    for mesh in meshes:
        vertices = mesh.Vertices
        num_triangles = mesh.NumTriangles

        for i in range(num_triangles):
            triangle = mesh.get_Triangle(i)
            try:
                val0 = triangle.get_Vertex(0)
                val1 = triangle.get_Vertex(1)
                val2 = triangle.get_Vertex(2)
            except:
                val0 = triangle.Vertex[0]
                val1 = triangle.Vertex[1]
                val2 = triangle.Vertex[2]

            p0 = val0 if isinstance(val0, DB.XYZ) else vertices[int(val0)]
            p1 = val1 if isinstance(val1, DB.XYZ) else vertices[int(val1)]
            p2 = val2 if isinstance(val2, DB.XYZ) else vertices[int(val2)]

            min_x = min(p0.X, p1.X, p2.X)
            max_x = max(p0.X, p1.X, p2.X)
            min_y = min(p0.Y, p1.Y, p2.Y)
            max_y = max(p0.Y, p1.Y, p2.Y)
            if px < min_x or px > max_x or py < min_y or py > max_y:
                continue

            d1 = (px - p1.X) * (p0.Y - p1.Y) - (p0.X - p1.X) * (py - p1.Y)
            d2 = (px - p2.X) * (p1.Y - p2.Y) - (p1.X - p2.X) * (py - p2.Y)
            d3 = (px - p0.X) * (p2.Y - p0.Y) - (p2.X - p0.X) * (py - p0.Y)

            has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
            has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)

            if not (has_neg and has_pos):
                v1 = p1 - p0
                v2 = p2 - p0
                nx = v1.Y * v2.Z - v1.Z * v2.Y
                ny = v1.Z * v2.X - v1.X * v2.Z
                nz = v1.X * v2.Y - v1.Y * v2.X

                if abs(nz) > 1e-6:
                    z_intersect = p0.Z - (nx * (px - p0.X) + ny * (py - p0.Y)) / nz
                    if best_z is None or z_intersect > best_z:
                        best_z = z_intersect

    return best_z


def parse_existing_z(param_value_str):
    try:
        if param_value_str and "," in param_value_str:
            parts = param_value_str.split(",")
            if len(parts) == 3:
                return float(parts[2])
    except:
        pass
    return 0.0


# =========================================================================
# 主处理流程
# =========================================================================

def process_elements(elements, dwg_instances, outline_uids, doc, view):
    output.show()
    
    if not elements:
        UI.TaskDialog.Show("提示", "没有找到有效的元素。")
        return

    base_e, base_n, base_angle = get_project_base_point_info(doc)
    outline_curves = get_outline_curves(doc, outline_uids)

    print("\n=================== 轮廓线加载状态检查 ===================")
    print("当前缓存的轮廓线段数: {} 条".format(len(outline_curves)))
    if len(outline_curves) == 0:
        print("❌ 警告：未检测到任何轮廓线！请先点击界面上的“框选航站楼轮廓线”按钮重新选择。")

    all_meshes = []
    for dwg in dwg_instances:
        all_meshes.extend(extract_meshes_from_dwg(dwg))

    print("\n=================== 开始执行坐标更新 ===================")
    print("待处理图元总量: {} 个 | DWG 网格数: {} 个".format(len(elements), len(all_meshes)))

    success_count = 0
    skipped_count = 0
    fallback_z_count = 0
    cat_ids = [DB.ElementId(c).IntegerValue for c in CATEGORIES]

    t = DB.Transaction(doc, "更新电气设备对角线坐标")
    t.Start()
    try:
        for idx, elem in enumerate(elements):
            if not elem:
                continue

            if not elem.Category or elem.Category.Id.IntegerValue not in cat_ids:
                continue

            elem_label = "图元 {} ({})".format(output.linkify(elem.Id), elem.Category.Name)

            param_AA = elem.LookupParameter("AA")
            param_BB = elem.LookupParameter("BB")
            val_AA = param_AA.AsInteger() if param_AA and param_AA.HasValue else 0
            val_BB = param_BB.AsInteger() if param_BB and param_BB.HasValue else 0

            if val_AA == 0 and val_BB == 0:
                skipped_count += 1
                continue

            diagonal_lines, found_subcats = collect_visible_diagonal_lines(elem, doc, view)

            if len(diagonal_lines) == 0:
                skipped_count += 1
                continue

            line = diagonal_lines[0]
            pt_0 = line.GetEndPoint(0)
            pt_1 = line.GetEndPoint(1)

            print("\n处理 {}：".format(elem_label))
            # 根据轮廓线精准分配 C1 (近端) 与 C2 (远端)
            pt_near, pt_far = determine_c1_c2_by_outline(pt_0, pt_1, outline_curves)

            str_p1, _ = convert_internal_to_survey_2d(pt_near, base_e, base_n, base_angle)
            str_p2, _ = convert_internal_to_survey_2d(pt_far, base_e, base_n, base_angle)

            pt_mid = DB.XYZ(
                (pt_near.X + pt_far.X) / 2.0,
                (pt_near.Y + pt_far.Y) / 2.0,
                (pt_near.Z + pt_far.Z) / 2.0
            )

            target_z_feet = ray_cast_z_to_mesh(pt_mid, all_meshes)
            if target_z_feet is not None:
                z_meters = to_meters(target_z_feet)
            else:
                old_param = elem.LookupParameter(PARAM_COORDI_C)
                old_str = old_param.AsString() if old_param else ""
                z_meters = parse_existing_z(old_str)
                fallback_z_count += 1

            str_pc, _ = convert_internal_to_survey_3d(pt_mid, z_meters, base_e, base_n, base_angle)

            p_c1 = elem.LookupParameter(PARAM_COORDI_1)
            p_c2 = elem.LookupParameter(PARAM_COORDI_2)
            p_cc = elem.LookupParameter(PARAM_COORDI_C)

            if p_c1 and not p_c1.IsReadOnly and p_c2 and not p_c2.IsReadOnly and p_cc and not p_cc.IsReadOnly:
                p_c1.Set(str_p1)
                p_c2.Set(str_p2)
                p_cc.Set(str_pc)
                success_count += 1
                output.print_md("- ✅ **{}**：`c1(近)`: {} | `c2(远)`: {} | `c`: {}".format(elem_label, str_p1, str_p2, str_pc))
            else:
                skipped_count += 1

        t.Commit()
    except Exception as ex:
        t.RollBack()
        import traceback
        output.print_md("### ❌ 发生异常，已回滚：\n```\n{}\n```".format(traceback.format_exc()))
        return

    summary = (
        "更新结果报告：\n"
        "• 成功更新: {} 个\n"
        "• 跳过/失败: {} 个\n"
        "• DWG投影失败保留旧Z值: {} 个"
    ).format(success_count, skipped_count, fallback_z_count)
    UI.TaskDialog.Show("完成", summary)


# =========================================================================
# 选择过滤器 & Handlers
# =========================================================================

class DwgSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, el):
        return isinstance(el, DB.ImportInstance)
    def AllowReference(self, r, p):
        return False

class OutlineSelectionFilter(UI.Selection.ISelectionFilter):
    def AllowElement(self, el):
        return isinstance(el, (DB.DetailCurve, DB.ModelCurve))
    def AllowReference(self, r, p):
        return False

class CustomCategoryFilter(UI.Selection.ISelectionFilter):
    def __init__(self, allowed_cats):
        self.allowed_cats = [DB.ElementId(c).IntegerValue for c in allowed_cats]
    def AllowElement(self, element):
        return element.Category and element.Category.Id.IntegerValue in self.allowed_cats
    def AllowReference(self, reference, position):
        return False

class AddDwgHandler(IExternalEventHandler):
    def __init__(self, window):
        self.window = window
    def Execute(self, app):
        uidoc = app.ActiveUIDocument
        if not uidoc: return
        try:
            ref = uidoc.Selection.PickObject(
                UI.Selection.ObjectType.Element, DwgSelectionFilter(), "请选择一个链接的DWG模型")
        except Exception as e:
            self.window.update_status(u"添加DWG已取消或出错: {}".format(e))
            return
        doc = uidoc.Document
        el = doc.GetElement(ref.ElementId)
        if el: self.window.add_dwg_item(el)
    def GetName(self): return "AddDwgHandler"

class PickOutlineHandler(IExternalEventHandler):
    def __init__(self, window):
        self.window = window
    def Execute(self, app):
        uidoc = app.ActiveUIDocument
        if not uidoc: return
        doc = uidoc.Document
        try:
            refs = uidoc.Selection.PickObjects(
                UI.Selection.ObjectType.Element, OutlineSelectionFilter(), "请在视图中框选航站楼轮廓线（支持框选多条）")
            uids = [doc.GetElement(r.ElementId).UniqueId for r in refs]
            self.window.save_outline_cache(uids)
            self.window.update_status(u"成功缓存 {} 条航站楼轮廓线。".format(len(uids)))
        except Exception as e:
            self.window.update_status(u"选择轮廓线已取消或出错: {}".format(e))
    def GetName(self): return "PickOutlineHandler"

class SelectByRectangleHandler(IExternalEventHandler):
    def __init__(self, window):
        self.window = window
    def Execute(self, app):
        uidoc = app.ActiveUIDocument
        if not uidoc: return
        doc = uidoc.Document
        try:
            selection_filter = CustomCategoryFilter(CATEGORIES)
            ref_elms = uidoc.Selection.PickElementsByRectangle(
                selection_filter, "请框选电气设备 (Electrical Fixtures)")
            elements = list(ref_elms)
        except Exception as e:
            self.window.update_status(u"框选被取消或出错: {}".format(e))
            return

        dwgs = self.window.get_dwg_instances()
        outlines = self.window.get_outline_uids()
        process_elements(elements, dwgs, outlines, doc, doc.ActiveView)
        self.window.update_status(u"框选处理完成。")
    def GetName(self): return "SelectByRectangleHandler"

class SelectAllHandler(IExternalEventHandler):
    def __init__(self, window):
        self.window = window
    def Execute(self, app):
        uidoc = app.ActiveUIDocument
        if not uidoc: return
        doc = uidoc.Document
        try:
            cat_list = [DB.ElementId(c) for c in CATEGORIES]
            from System.Collections.Generic import List
            net_cat_list = List[DB.ElementId](cat_list)
            filter_cat = DB.ElementMulticategoryFilter(net_cat_list)
            collector = DB.FilteredElementCollector(doc).WherePasses(filter_cat).WhereElementIsNotElementType()
            elements = list(collector)
        except Exception as e:
            self.window.update_status(u"全选处理出错: {}".format(e))
            return

        dwgs = self.window.get_dwg_instances()
        outlines = self.window.get_outline_uids()
        process_elements(elements, dwgs, outlines, doc, doc.ActiveView)
        self.window.update_status(u"全选处理完成。")
    def GetName(self): return "SelectAllHandler"


# =========================================================================
# WPF 界面
# =========================================================================

class DwgItem(object):
    def __init__(self, instance):
        self.Instance = instance
        self.Name = instance.Category.Name if instance.Category else "未命名DWG"
        self.UniqueId = instance.UniqueId

class CoordsWindow(WPFWindow):
    def __init__(self, xaml_file_name):
        WPFWindow.__init__(self, xaml_file_name)
        self.uidoc = __revit__.ActiveUIDocument
        self.doc = self.uidoc.Document

        self.add_dwg_handler = AddDwgHandler(self)
        self.add_dwg_event = ExternalEvent.Create(self.add_dwg_handler)

        self.pick_outline_handler = PickOutlineHandler(self)
        self.pick_outline_event = ExternalEvent.Create(self.pick_outline_handler)

        self.select_rect_handler = SelectByRectangleHandler(self)
        self.select_rect_event = ExternalEvent.Create(self.select_rect_handler)

        self.select_all_handler = SelectAllHandler(self)
        self.select_all_event = ExternalEvent.Create(self.select_all_handler)

        self.outline_uids = []
        self.load_cached_dwgs()
        self.load_cached_outlines()

    def OnWindowDrag(self, sender, args):
        try: self.DragMove()
        except: pass

    def load_cached_dwgs(self):
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    uids = json.load(f)
                for uid in uids:
                    try:
                        el = self.doc.GetElement(uid)
                        if isinstance(el, DB.ImportInstance):
                            self.lstDwgs.Items.Add(DwgItem(el))
                    except: pass
            except: pass

    def save_cached_dwgs(self):
        uids = [item.UniqueId for item in self.lstDwgs.Items]
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(uids, f)
        except: pass

    def load_cached_outlines(self):
        if os.path.exists(OUTLINE_CACHE_FILE):
            try:
                with open(OUTLINE_CACHE_FILE, "r") as f:
                    self.outline_uids = json.load(f)
                self.update_status(u"已加载缓存的轮廓线（共 {} 条）。".format(len(self.outline_uids)))
            except: pass

    def save_outline_cache(self, uids):
        self.outline_uids = uids
        try:
            with open(OUTLINE_CACHE_FILE, "w") as f:
                json.dump(uids, f)
        except: pass

    def get_outline_uids(self):
        return self.outline_uids

    def add_dwg_item(self, el):
        exists = any(item.UniqueId == el.UniqueId for item in self.lstDwgs.Items)
        if not exists:
            self.lstDwgs.Items.Add(DwgItem(el))
            self.save_cached_dwgs()
            self.update_status(u"已添加DWG：{}".format(el.Id.IntegerValue))
        else:
            self.update_status(u"该DWG已在列表中。")

    def get_dwg_instances(self):
        return [item.Instance for item in self.lstDwgs.Items]

    def update_status(self, msg):
        try: self.txt_status.Text = msg
        except: print(msg)

    def OnAddDwg(self, sender, args):
        self.update_status(u"请在 Revit 视图中选择一个 DWG...")
        self.add_dwg_event.Raise()

    def OnRemoveDwg(self, sender, args):
        selected = self.lstDwgs.SelectedItem
        if selected:
            self.lstDwgs.Items.Remove(selected)
            self.save_cached_dwgs()
            self.update_status(u"已移除选中的DWG。")
        else:
            self.update_status(u"请先在列表中选中要移除的DWG。")

    def OnPickOutlineClick(self, sender, args):
        self.update_status(u"请在 Revit 视图中框选航站楼轮廓线...")
        self.pick_outline_event.Raise()

    def OnSelectClick(self, sender, args):
        self.update_status(u"请在 Revit 视图中框选电气设备...")
        self.select_rect_event.Raise()

    def OnAllClick(self, sender, args):
        self.update_status(u"正在收集项目内全部电气设备并计算...")
        self.select_all_event.Raise()

if __name__ == "__main__":
    win = CoordsWindow("ui.xaml")
    win.show()