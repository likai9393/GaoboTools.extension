# -*- coding: utf-8 -*-
__title__ = 'PickElementInfo'
__doc__ = "点击按钮激活功能：显示当前选中元素的族名称、类型名称、元素 ID，以及可见的父级主视图（极速版）。小窗口保持悬浮。"
__author__ = 'Gaobo'

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')

from pyrevit import forms
from Autodesk.Revit.UI import IExternalEventHandler, ExternalEvent

# ---------------- 1. 线程安全的外部事件处理类 ----------------

class FetchSelectionHandler(IExternalEventHandler):
    def __init__(self, window):
        self.window = window

    def Execute(self, app):
        try:
            import Autodesk.Revit.DB as DB

            uidoc = app.ActiveUIDocument
            if not uidoc:
                self.window.update_status("未找到活动的 Revit 文档！")
                return

            doc = uidoc.Document
            selection_ids = uidoc.Selection.GetElementIds()

            if not selection_ids or len(selection_ids) == 0:
                self.window.update_status("提示：当前未选中任何元素！请先在视图中选择元素。")
                return

            # 只提取父级/独立主视图
            primary_views = self._get_primary_views_only(doc, DB)

            results = []
            for elem_id in selection_ids:
                elem = doc.GetElement(elem_id)
                if elem:
                    data = self._parse_element_primary_only(elem, doc, primary_views, DB)
                    results.append(data)

            self.window.update_results(results)

        except Exception as ex:
            self.window.update_status("读取出错: " + str(ex))

    def _get_primary_views_only(self, doc, DB):
        """
        过滤真正的父级/主视图
        """
        collector = DB.FilteredElementCollector(doc).OfClass(DB.View)
        views = []
        
        exclude_types = [
            DB.ViewType.DrawingSheet, 
            DB.ViewType.Schedule, 
            DB.ViewType.Internal, 
            DB.ViewType.ProjectBrowser, 
            DB.ViewType.SystemBrowser
        ]

        for v in collector:
            if v.IsTemplate or v.ViewType in exclude_types:
                continue
                
            primary_id = v.GetPrimaryViewId()
            if primary_id and primary_id.ToString() != "-1":
                continue

            views.append(v)
            
        return views

    def _safe_get_name(self, obj, default="<无名称>"):
        if not obj:
            return default
        try:
            if hasattr(obj, 'Name') and obj.Name:
                return str(obj.Name)
        except:
            pass
        return default

    def _get_view_type_label(self, view, DB):
        """
        将 Revit 视图类型转换为直观的中文名称
        """
        v_type = view.ViewType
        if v_type == DB.ViewType.ThreeD:
            return "三维视图"
        elif v_type == DB.ViewType.FloorPlan:
            return "楼层平面"
        elif v_type == DB.ViewType.CeilingPlan:
            return "天花板平面"
        elif v_type == DB.ViewType.Elevation:
            return "立面视图"
        elif v_type == DB.ViewType.Section:
            return "剖面视图"
        elif v_type == DB.ViewType.EngineeringPlan:
            return "结构平面"
        elif v_type == DB.ViewType.AreaPlan:
            return "面积平面"
        elif v_type == DB.ViewType.DraftingView:
            return "绘图视图"
        elif v_type == DB.ViewType.Legend:
            return "图例"
        else:
            return str(v_type)

    def _parse_element_primary_only(self, elem, doc, primary_views, DB):
        # 1. 获取 Element ID
        id_str = elem.Id.ToString()

        # 2. 获取族名称与类型名称
        fam_name = "<系统族/无族名>"
        type_name = "<无类型>"

        try:
            type_id = elem.GetTypeId()
            if type_id and type_id.ToString() != "-1":
                t_elem = doc.GetElement(type_id)
                if t_elem:
                    # 类型名称
                    type_name = self._safe_get_name(t_elem, default=elem.Name if hasattr(elem, 'Name') else "<未知名类型>")
                    # 族名称
                    if hasattr(t_elem, 'FamilyName') and t_elem.FamilyName:
                        fam_name = str(t_elem.FamilyName)
                    elif hasattr(t_elem, 'Category') and t_elem.Category:
                        fam_name = "[系统族] " + str(t_elem.Category.Name)
            else:
                type_name = self._safe_get_name(elem, default="<实例元素>")
                if hasattr(elem, 'Category') and elem.Category:
                    fam_name = str(elem.Category.Name)
        except:
            type_name = self._safe_get_name(elem, default="<解析类型失败>")

        # 3. 极速父视图范围匹配（附带视图类型）
        visible_view_info = []

        # 二维构件直接找宿主视图
        if hasattr(elem, 'OwnerViewId') and elem.OwnerViewId and elem.OwnerViewId.ToString() != "-1":
            v = doc.GetElement(elem.OwnerViewId)
            if v:
                v_name = self._safe_get_name(v)
                v_type = self._get_view_type_label(v, DB)
                visible_view_info.append("{} [{}]".format(v_name, v_type))
        else:
            # 三维模型构件：只在父级主视图中做快速 BoundingBox 碰撞测试
            elem_bbox = elem.get_BoundingBox(None)

            if elem_bbox:
                outline = DB.Outline(elem_bbox.Min, elem_bbox.Max)
                bbox_filter = DB.BoundingBoxIntersectsFilter(outline)

                for view in primary_views:
                    try:
                        found = DB.FilteredElementCollector(doc, view.Id)\
                                  .WherePasses(bbox_filter)\
                                  .ToElementIds()
                        if elem.Id in found:
                            v_name = self._safe_get_name(view)
                            v_type = self._get_view_type_label(view, DB)
                            visible_view_info.append("{} [{}]".format(v_name, v_type))
                    except:
                        continue

        if visible_view_info:
            views_str = " ; ".join(visible_view_info)
        else:
            views_str = "<未在任何主视图中找到>"

        class InnerRow(object):
            def __init__(self, e_id, f_name, t_name, v_names):
                self.elem_id = e_id
                self.fam_name = f_name
                self.type_name = t_name
                self.view_name = v_names

        return InnerRow(id_str, fam_name, type_name, views_str)

    def GetName(self):
        return "FetchSelectionHandler"


# ---------------- 2. WPF 界面 ----------------

XAML_VIEW = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="元素信息查看器 (主视图极速版)" Width="820" Height="480"
        Topmost="True" WindowStartupLocation="CenterScreen">
    <Grid Margin="12">
        <Grid.RowDefinitions>
            <RowDefinition Height="Auto"/>
            <RowDefinition Height="*"/>
        </Grid.RowDefinitions>
        
        <StackPanel Grid.Row="0" Margin="0,0,0,10">
            <Button Name="btn_get_selection" Content="读取当前选中的元素" Height="36" FontSize="13" FontWeight="Bold" Background="#007ACC" Foreground="White"/>
            <TextBlock Name="txt_status" Text="提示：先在 Revit 中选中元素，再点击上方按钮。" Foreground="Gray" FontSize="11" Margin="0,6,0,0" TextWrapping="Wrap"/>
        </StackPanel>
        
        <ListView Name="lst_result" Grid.Row="1" FontSize="12">
            <ListView.View>
                <GridView>
                    <GridViewColumn Header="元素 ID" Width="85" DisplayMemberBinding="{Binding elem_id}"/>
                    <GridViewColumn Header="族名称" Width="140" DisplayMemberBinding="{Binding fam_name}"/>
                    <GridViewColumn Header="元素类型名称" Width="160" DisplayMemberBinding="{Binding type_name}"/>
                    <GridViewColumn Header="可见的父级主视图 [视图类型]" Width="390" DisplayMemberBinding="{Binding view_name}"/>
                </GridView>
            </ListView.View>
        </ListView>
    </Grid>
</Window>
"""

class ElementPickerWindow(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, XAML_VIEW, literal_string=True)
        self.handler = FetchSelectionHandler(self)
        self.ext_event = ExternalEvent.Create(self.handler)
        self.btn_get_selection.Click += self.on_btn_click

    def on_btn_click(self, sender, e):
        self.txt_status.Text = "正在扫描父级主视图，请稍候..."
        self.ext_event.Raise()

    def update_results(self, data):
        self.lst_result.ItemsSource = None
        self.lst_result.ItemsSource = data
        self.txt_status.Text = "成功读取 {} 个元素的信息！".format(len(data))

    def update_status(self, msg):
        self.txt_status.Text = msg


# ---------------- 3. 主入口 ----------------

if __name__ == '__main__':
    win = ElementPickerWindow()
    win.show()