# -*- coding: utf-8 -*-
__title__ = "Extend / Trim"
__doc__ = "点击按钮激活功能：点选任意参照（墙、线、族边缘），框选目标元素轴向延伸/缩短。小窗口保持悬浮。"
__author__ = 'Gaobo'

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

import Autodesk
from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *
from Autodesk.Revit.UI.Selection import *
from pyrevit import revit, forms, script

uidoc = revit.uidoc
doc = revit.doc

# ----------------- 1. Selection Filter -----------------

class EdgeOrCurveSelectionFilter(ISelectionFilter):
    def AllowElement(self, elem):
        if not elem:
            return False
        if isinstance(elem, (CurveElement, Wall, FamilyInstance)):
            return True
        return False

    def AllowReference(self, reference, point):
        return True

# ----------------- 2. Geometry Core -----------------

def is_linear_element(elem):
    if not elem or not elem.Location:
        return False
    return isinstance(elem.Location, LocationCurve)

def get_line_from_reference(picked_ref):
    try:
        elem = doc.GetElement(picked_ref)
        if not elem:
            return None

        if picked_ref.ElementReferenceType == ElementReferenceType.REFERENCE_EDGE:
            geo_obj = elem.GetGeometryObjectFromReference(picked_ref)
            if isinstance(geo_obj, Edge):
                return geo_obj.AsCurve()

        if isinstance(elem, CurveElement):
            return elem.GeometryCurve

        if isinstance(elem, Wall) and isinstance(elem.Location, LocationCurve):
            return elem.Location.Curve

        if isinstance(elem, FamilyInstance):
            opt = Options()
            opt.ComputeReferences = True
            geom_elem = elem.get_Geometry(opt)
            if geom_elem:
                for g_obj in geom_elem:
                    if isinstance(g_obj, GeometryInstance):
                        for inst_obj in g_obj.GetInstanceGeometry():
                            if isinstance(inst_obj, Line):
                                return inst_obj
                    elif isinstance(g_obj, Line):
                        return g_obj
    except Exception:
        pass
    return None

def get_target_plane(picked_ref):
    picked_xyz = picked_ref.GlobalPoint
    curve = get_line_from_reference(picked_ref)
    
    if curve and isinstance(curve, Line):
        p0 = curve.GetEndPoint(0)
        p1 = curve.GetEndPoint(1)
        dir_vec = (p1 - p0).Normalize()
        
        if abs(dir_vec.Z) > 0.99:
            plane_normal = XYZ.BasisX
        else:
            plane_normal = dir_vec.CrossProduct(XYZ.BasisZ).Normalize()
        
        return Plane.CreateByNormalAndOrigin(plane_normal, p0)
    else:
        active_view = doc.ActiveView
        view_dir = active_view.ViewDirection
        return Plane.CreateByNormalAndOrigin(view_dir, picked_xyz)

def get_line_plane_intersection(target_line, plane):
    try:
        line_origin = target_line.GetEndPoint(0)
        line_dir = target_line.Direction.Normalize()
        plane_normal = plane.Normal
        plane_origin = plane.Origin
        
        denom = line_dir.DotProduct(plane_normal)
        if abs(denom) < 1e-6:
            return None
            
        t = (plane_origin - line_origin).DotProduct(plane_normal) / denom
        return line_origin + line_dir * t
    except Exception:
        return None

def run_extend_trim_core():
    sel_filter = EdgeOrCurveSelectionFilter()
    try:
        picked_ref = uidoc.Selection.PickObject(
            ObjectType.PointOnElement, 
            sel_filter, 
            "【1/2】请点击参照边界（墙基线、模型线或族边线）"
        )
    except Autodesk.Revit.Exceptions.OperationCanceledException:
        return

    if not picked_ref:
        return

    ref_elem = doc.GetElement(picked_ref)
    target_plane = get_target_plane(picked_ref)

    try:
        picked_elements = uidoc.Selection.PickElementsByRectangle("【2/2】请框选需要延伸/缩短的构件")
    except Autodesk.Revit.Exceptions.OperationCanceledException:
        return

    if not picked_elements:
        return

    success_count = 0
    min_length = doc.Application.ShortCurveTolerance

    with revit.Transaction("沿轴向对齐伸缩"):
        for elem in picked_elements:
            if not is_linear_element(elem) or (ref_elem and elem.Id == ref_elem.Id):
                continue
                
            loc_curve = elem.Location
            target_curve = loc_curve.Curve
            if not isinstance(target_curve, Line):
                continue 
            
            p0 = target_curve.GetEndPoint(0)
            p1 = target_curve.GetEndPoint(1)
            
            intersect_pt = get_line_plane_intersection(target_curve, target_plane)
            if intersect_pt is None:
                continue
            
            dist0 = p0.DistanceTo(intersect_pt)
            dist1 = p1.DistanceTo(intersect_pt)
            
            if dist0 < dist1:
                fixed_pt = p1
            else:
                fixed_pt = p0
                
            new_length = fixed_pt.DistanceTo(intersect_pt)
            if new_length < min_length:
                continue
                
            try:
                if dist0 < dist1:
                    new_curve = Line.CreateBound(intersect_pt, fixed_pt)
                else:
                    new_curve = Line.CreateBound(fixed_pt, intersect_pt)
                    
                loc_curve.Curve = new_curve
                success_count += 1
            except Exception:
                pass

    if success_count > 0:
        forms.toast("成功处理 {} 个构件".format(success_count), title="执行成功")

# ----------------- 3. UI logic with pyRevit Forms Native External Event -----------------

XAML_BUILD = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Extend / Trim" Width="220" Height="100"
        Topmost="True" WindowStartupLocation="CenterScreen" ResizeMode="NoResize">
    <Grid Margin="10">
        <Button Name="btn_pick" Content="拾取线并对齐" Height="40" FontSize="14" FontWeight="Bold"/>
    </Grid>
</Window>
"""

class ExtendTrimTool(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_BUILD, literal_string=True)
        self.btn_pick.Click += self.on_click_pick

    def on_click_pick(self, sender, e):
        # Hide the floating form to allow Revit UI to accept mouse click input
        self.hide()
        
        try:
            run_extend_trim_core()
        except Exception as ex:
            forms.alert("执行出错: {}".format(str(ex)))
        finally:
            # Re-show the window after selection and processing is complete
            self.show()

# ----------------- Entry Point -----------------

if __name__ == "__main__":
    win = ExtendTrimTool()
    win.show()