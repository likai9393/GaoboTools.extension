# -*- coding: utf-8 -*-
__title__ = 'ExportCoords'
__doc__ = ('一键刷新水电井的四角坐标并投影DWG获取Z值（顺时针/逆时针闭合排序）。\n'
           '族内2条对角线由 AA/BB 参数控制显隐；即使当前处于隐藏状态，'
           '处理时也会临时强制显示读取几何，读取完毕后自动还原AA/BB原值，不影响模型最终状态。')
__author__ = 'Gaobo'

import math
import os
import json
from pyrevit import revit, DB, UI
from pyrevit import script
from pyrevit.forms import WPFWindow

# 配置参数
BASE_E = 809800.0
BASE_N = 819300.0
BASE_Z = 0.0
ROTATION_DEG = 19.0
ROTATION_RAD = math.radians(ROTATION_DEG)

MIN_LINE_LENGTH_FEET = 400.0 / 304.8

# 合格线除了长度达标，还必须是这个线子类别(Subcategory)
TARGET_LINE_SUBCATEGORY = "Diagonal"

# 控制2条对角线可见性的两个 Yes/No 参数名（其中一个可写作为"主控"，另一个通常是公式联动的只读值）
AA_PARAM_NAME = "AA"
BB_PARAM_NAME = "BB"

CATEGORIES = [
    DB.BuiltInCategory.OST_ElectricalFixtures,
    DB.BuiltInCategory.OST_PlumbingFixtures
]

PARAM_NAMES = ["P1", "P2", "P3", "P4"]

CACHE_FILE = os.path.join(os.path.dirname(__file__), "dwg_cache.json")

output = script.get_output()


def to_meters(feet_val):
    return feet_val * 0.3048


def to_feet(meters_val):
    return meters_val / 0.3048


def transform_to_project_coords(local_pt, computed_z_meters):
    """转换至项目坐标，Z直接使用投影算出的米单位值（或回退值）"""
    x, y = local_pt.X, local_pt.Y
    e_rot = x * math.cos(ROTATION_RAD) - y * math.sin(ROTATION_RAD)
    n_rot = x * math.sin(ROTATION_RAD) + y * math.cos(ROTATION_RAD)
    e_final = BASE_E + to_meters(e_rot)
    n_final = BASE_N + to_meters(n_rot)
    return e_final, n_final, computed_z_meters


def sort_points_clockwise(points):
    """通过计算极角，将4个点按环状排序，防止生成坐标跳跃"""
    if len(points) != 4:
        return points

    cx = sum(p.X for p in points) / 4.0
    cy = sum(p.Y for p in points) / 4.0

    sorted_pts = sorted(points, key=lambda p: math.atan2(p.Y - cy, p.X - cx))
    return sorted_pts


def get_line_subcategory_name(line, doc):
    """读取一条模型线所属的线子类别(Subcategory)名称，取不到返回 None"""
    try:
        style_id = line.GraphicsStyleId
        if style_id and style_id != DB.ElementId.InvalidElementId:
            style = doc.GetElement(style_id)
            if style and style.GraphicsStyleCategory:
                return style.GraphicsStyleCategory.Name
    except:
        pass
    return None


def collect_model_lines(element):
    """
    收集族实例几何中所有的模型线(DB.Line)，并附带诊断信息。
    注意：调用这个函数之前，如果线是靠AA/BB这类Yes/No参数关联"Visible"属性来控制显隐的，
    必须先把该参数临时设为可见状态并 Regenerate()，否则被隐藏的线根本不会出现在几何结果里
    （这种隐藏是"不生成该几何"，Options.IncludeNonVisibleObjects 救不回来）。
    """
    doc = element.Document
    options = DB.Options()
    options.ComputeReferences = True
    options.IncludeNonVisibleObjects = True
    geom_elem = element.get_Geometry(options)

    raw_lines = []

    def _walk(geo):
        if not geo:
            return
        for geom_obj in geo:
            if isinstance(geom_obj, DB.GeometryInstance):
                _walk(geom_obj.GetInstanceGeometry())
            elif isinstance(geom_obj, DB.Line):
                raw_lines.append(geom_obj)

    _walk(geom_elem)

    info_list = []
    for line in raw_lines:
        subcat_name = get_line_subcategory_name(line, doc)
        info_list.append({
            "line": line,
            "length_mm": line.Length * 304.8,
            "length_ok": line.Length >= MIN_LINE_LENGTH_FEET,
            "subcategory": subcat_name,
            "subcategory_ok": subcat_name == TARGET_LINE_SUBCATEGORY,
        })
    return info_list


def get_qualified_lines(info_list):
    """从 collect_model_lines() 的结果中，筛出同时满足：长度>=400mm 且 子类别=Diagonal 的线"""
    return [info["line"] for info in info_list if info["length_ok"] and info["subcategory_ok"]]


def extract_meshes_from_dwg(dwg_instance):
    """从DWG导入实例中提取所有的Mesh网格面"""
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
    """从点pt垂直方向对多个Mesh进行射线相交检查，返回相交点的绝对Z(英尺)，找不到返回None"""
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
    """从已有的 'X,Y,Z' 文本参数中解析出旧的 Z 值（米），解析失败则返回 0.0"""
    try:
        if param_value_str and "," in param_value_str:
            parts = param_value_str.split(",")
            if len(parts) == 3:
                return float(parts[2])
    except:
        pass
    return 0.0


def force_set_param(param, value):
    """
    尝试把参数设为指定值，返回是否真的执行了设置（用于后续判断要不要还原）。
    只读/找不到参数/设置异常时都视为"没能强制"，不报错、不中断流程。
    """
    if not param or param.IsReadOnly:
        return False
    try:
        param.Set(value)
        return True
    except:
        return False


def process_elements(elements, dwg_instances):
    if not elements:
        UI.TaskDialog.Show("提示", "没有找到有效的元素。")
        return

    doc = elements[0].Document

    all_meshes = []
    for dwg in dwg_instances:
        all_meshes.extend(extract_meshes_from_dwg(dwg))

    print("开始处理 {} 个设备图元，正在与 {} 个DWG的网格面进行投影计算...".format(
        len(elements), len(dwg_instances)))

    success_count = 0
    skipped_count = 0
    fallback_z_count = 0
    forced_visibility_count = 0  # 统计有多少个元素是靠临时强制AA/BB才读到几何的
    cat_ids = [DB.ElementId(c).IntegerValue for c in CATEGORIES]

    with revit.Transaction("写入四角投影坐标"):
        for elem in elements:
            if not elem.Category or elem.Category.Id.IntegerValue not in cat_ids:
                continue

            cat_name = elem.Category.Name if elem.Category else "<未知类别>"
            elem_label = "元素 {} (类别:{})".format(output.linkify(elem.Id), cat_name)

            # ---- 【改造】AA/BB 是互斥的（一个可写的"主控"，另一个是公式联动的只读值），
            # 不能同时强制成1。必须分两遍：主控=1读一条线，主控=0再读另一条线，最后合并。
            param_AA = elem.LookupParameter(AA_PARAM_NAME)
            param_BB = elem.LookupParameter(BB_PARAM_NAME)
            old_AA = param_AA.AsInteger() if param_AA else None
            old_BB = param_BB.AsInteger() if param_BB else None

            master_param, master_name, master_old_val = None, None, None
            if param_AA is not None and not param_AA.IsReadOnly:
                master_param, master_name, master_old_val = param_AA, "AA", old_AA
            elif param_BB is not None and not param_BB.IsReadOnly:
                master_param, master_name, master_old_val = param_BB, "BB", old_BB

            vis_status_str = "AA={}({}); BB={}({})".format(
                old_AA if old_AA is not None else "<不存在>",
                "只读" if (param_AA and param_AA.IsReadOnly) else ("不存在" if param_AA is None else "可写"),
                old_BB if old_BB is not None else "<不存在>",
                "只读" if (param_BB and param_BB.IsReadOnly) else ("不存在" if param_BB is None else "可写"))

            def _read_pass(state_value):
                force_set_param(master_param, state_value)
                doc.Regenerate()
                info_list = collect_model_lines(elem)
                qualified = get_qualified_lines(info_list)
                return info_list, qualified

            try:
                if master_param is not None:
                    forced_visibility_count += 1
                    info_list_1, qualified_1 = _read_pass(1)
                    info_list_2, qualified_2 = _read_pass(0)
                    qualified_lines = qualified_1 + qualified_2
                    pass_note = (
                        "已切换 {}=1/0 两种状态分别读取：状态1找到{}条合格线，状态2找到{}条合格线。".format(
                            master_name, len(qualified_1), len(qualified_2)))
                else:
                    # 两个参数都只读/都不存在，没法切换状态，只能按当前可见状态读一次
                    info_list_1, qualified_1 = collect_model_lines(elem), None
                    qualified_1 = get_qualified_lines(info_list_1)
                    info_list_2, qualified_2 = [], []
                    qualified_lines = qualified_1
                    pass_note = "AA/BB均只读或不存在，无法切换状态，仅按当前可见状态读取了一次。"

                # ---- 诊断：两遍合计应该正好凑出 2 条线 ----
                if len(qualified_lines) != 2:
                    skipped_count += 1

                    def _describe_pass(info_list, qualified):
                        total = len(info_list)
                        wrong = [i for i in info_list if i["length_ok"] and not i["subcategory_ok"]]
                        wrong_names = "; ".join(
                            "{}(长度{:.0f}mm)".format(i["subcategory"] or "<无子类别>", i["length_mm"])
                            for i in wrong) or "无"
                        return "共{}条线，合格{}条，长度够但子类别不对的有：{}".format(
                            total, len(qualified), wrong_names)

                    detail_1 = _describe_pass(info_list_1, qualified_1) if info_list_1 else "（未读取）"
                    detail_2 = _describe_pass(info_list_2, qualified_2) if info_list_2 else "（未读取）"

                    output.print_md(
                        "- {}：参数状态 {}。{}\n"
                        "  状态1(主控=1)：{}\n"
                        "  状态2(主控=0)：{}\n"
                        "  两遍合计找到 {} 条合格线（需要正好2条），已跳过。".format(
                            elem_label, vis_status_str, pass_note,
                            detail_1, detail_2, len(qualified_lines)))
                    continue

                pts = []
                for line in qualified_lines:
                    pts.append(line.GetEndPoint(0))
                    pts.append(line.GetEndPoint(1))
                pts = sort_points_clockwise(pts)

                if len(pts) != 4:
                    skipped_count += 1
                    output.print_md("- {}：端点数量异常（{}个），已跳过。".format(elem_label, len(pts)))
                    continue

                coords_str = []
                for i, pt in enumerate(pts):
                    target_z_feet = ray_cast_z_to_mesh(pt, all_meshes)

                    if target_z_feet is not None:
                        z_meters = to_meters(target_z_feet)
                    else:
                        old_param = elem.LookupParameter(PARAM_NAMES[i])
                        old_str = old_param.AsString() if old_param else ""
                        z_meters = parse_existing_z(old_str)
                        fallback_z_count += 1

                    e, n, z = transform_to_project_coords(pt, z_meters)
                    coords_str.append("{:.3f},{:.3f},{:.3f}".format(e, n, z))

                has_all_params = True
                for i, p_name in enumerate(PARAM_NAMES):
                    param = elem.LookupParameter(p_name)
                    if param and not param.IsReadOnly:
                        param.Set(coords_str[i])
                    else:
                        has_all_params = False
                if has_all_params:
                    success_count += 1
                else:
                    skipped_count += 1
                    output.print_md("- {}：P1~P4 参数缺失或只读，未能完整写入。".format(elem_label))

            finally:
                # ---- 无论上面成功/跳过，都要把主控参数还原成原来的值，不留痕迹 ----
                if master_param is not None and master_old_val is not None:
                    force_set_param(master_param, master_old_val)
                    doc.Regenerate()

    summary = (
        "成功更新了 {} 个元素的投影坐标。\n"
        "跳过 {} 个元素（详情见输出窗口）。\n"
        "其中有 {} 个点因DWG投影失败，Z值已保留原参数中的旧数据。\n"
        "有 {} 个元素是通过临时强制显示AA/BB对角线才读取到几何的（处理后已还原原状态）。"
    ).format(success_count, skipped_count, fallback_z_count, forced_visibility_count)
    UI.TaskDialog.Show("完成", summary)


# ---- UI 类 ----
class DwgItem(object):
    def __init__(self, instance):
        self.Instance = instance
        self.Name = instance.Category.Name if instance.Category else "未命名DWG"
        self.UniqueId = instance.UniqueId


class CoordsWindow(WPFWindow):
    def __init__(self, xaml_file_name):
        WPFWindow.__init__(self, xaml_file_name)
        self.doc = revit.doc
        self.uidoc = revit.uidoc

        self.load_cached_dwgs()

    def OnWindowDrag(self, sender, args):
        try:
            self.DragMove()
        except:
            pass

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
                    except:
                        pass
            except:
                pass

    def save_cached_dwgs(self):
        uids = [item.UniqueId for item in self.lstDwgs.Items]
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(uids, f)
        except:
            pass

    def OnAddDwg(self, sender, args):
        self.Hide()
        try:
            class DwgSelectionFilter(UI.Selection.ISelectionFilter):
                def AllowElement(self, el):
                    return isinstance(el, DB.ImportInstance)

                def AllowReference(self, r, p):
                    return False

            ref = self.uidoc.Selection.PickObject(
                UI.Selection.ObjectType.Element, DwgSelectionFilter(), "请选择一个链接的DWG模型")
            el = self.doc.GetElement(ref.ElementId)

            exists = any(item.UniqueId == el.UniqueId for item in self.lstDwgs.Items)
            if not exists:
                self.lstDwgs.Items.Add(DwgItem(el))
                self.save_cached_dwgs()
        except:
            pass
        self.ShowDialog()

    def OnRemoveDwg(self, sender, args):
        selected = self.lstDwgs.SelectedItem
        if selected:
            self.lstDwgs.Items.Remove(selected)
            self.save_cached_dwgs()

    def OnSelectClick(self, sender, args):
        self.Hide()
        try:
            selection_filter = CustomCategoryFilter(CATEGORIES)
            ref_elms = self.uidoc.Selection.PickElementsByRectangle(
                selection_filter, "请框选电器和管道固定装置")
            elements = list(ref_elms)
            dwgs = [item.Instance for item in self.lstDwgs.Items]
            process_elements(elements, dwgs)
        except Exception as e:
            print("框选被取消或出错: {}".format(e))
        self.ShowDialog()

    def OnAllClick(self, sender, args):
        self.Hide()
        try:
            cat_list = [DB.ElementId(c) for c in CATEGORIES]
            from System.Collections.Generic import List
            net_cat_list = List[DB.ElementId](cat_list)
            filter_cat = DB.ElementMulticategoryFilter(net_cat_list)
            collector = DB.FilteredElementCollector(self.doc).WherePasses(filter_cat).WhereElementIsNotElementType()
            elements = list(collector)
            dwgs = [item.Instance for item in self.lstDwgs.Items]
            process_elements(elements, dwgs)
        except Exception as e:
            print("全选处理出错: {}".format(e))
        self.ShowDialog()


class CustomCategoryFilter(UI.Selection.ISelectionFilter):
    def __init__(self, allowed_cats):
        self.allowed_cats = [DB.ElementId(c).IntegerValue for c in allowed_cats]

    def AllowElement(self, element):
        return element.Category and element.Category.Id.IntegerValue in self.allowed_cats

    def AllowReference(self, reference, position):
        return False


if __name__ == "__main__":
    doc = revit.doc
    uidoc = revit.uidoc

    win = CoordsWindow("ui.xaml")
    win.ShowDialog()
    # 框选/全选 + 投影计算 + 写参数 都已在窗口按钮事件内部完成；
    # 只有用户点击窗口右上角的 X 关闭窗口后，ShowDialog() 才会返回，脚本随之结束。
