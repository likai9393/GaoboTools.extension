# -*- coding: utf-8 -*-
__title__ = 'Schedule'
__doc__ = '快速弹出所有明细表列表，点击打开'
__author__ = 'Gaobo'

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from Autodesk.Revit.DB import FilteredElementCollector, ViewSchedule
from pyrevit import revit, HOST_APP

import System
from System.Windows import (
    Window, WindowStartupLocation, ResizeMode,
    Thickness, VerticalAlignment, HorizontalAlignment,
    TextWrapping, FontWeights
)
from System.Windows.Controls import (
    StackPanel, DockPanel, ListBox, ListBoxItem,
    TextBox, TextBlock, Button, Border, ScrollViewer,
    Orientation, Dock
)
from System.Windows.Media import SolidColorBrush, Color, Colors
from System.Windows.Input import Key

doc = revit.doc
uidoc = HOST_APP.uidoc


def get_all_schedules():
    """获取当前项目所有明细表（排除修订明细表等内部表）"""
    collector = FilteredElementCollector(doc).OfClass(ViewSchedule)
    schedules = []
    for s in collector:
        if not s.IsTemplate and not s.IsTitleblockRevisionSchedule:
            schedules.append(s)
    schedules.sort(key=lambda s: s.Name)
    return schedules


def open_schedule(schedule_id):
    """在 Revit 中打开指定明细表视图"""
    uidoc.ActiveView = doc.GetElement(schedule_id)


class SheetNavigatorWindow(Window):
    def __init__(self):
        self.Title = "明细表导航"
        self.Width = 320
        self.Height = 480
        self.MinWidth = 220
        self.MinHeight = 200
        self.WindowStartupLocation = WindowStartupLocation.CenterScreen
        self.ResizeMode = ResizeMode.CanResizeWithGrip
        self.Topmost = True

        self.all_schedules = get_all_schedules()
        self._build_ui()
        self._populate_list(self.all_schedules)

    def _build_ui(self):
        root = DockPanel()
        root.Margin = Thickness(0)

        # ── 顶部：标题 + 搜索框 ──
        top_panel = StackPanel()
        top_panel.Background = SolidColorBrush(Color.FromRgb(245, 245, 245))
        DockPanel.SetDock(top_panel, Dock.Top)

        # 标题栏
        title_bar = Border()
        title_bar.Background = SolidColorBrush(Color.FromRgb(60, 90, 140))
        title_bar.Padding = Thickness(12, 8, 12, 8)
        title_text = TextBlock()
        title_text.Text = "📋  明细表列表  ({} 张)".format(len(self.all_schedules))
        title_text.Foreground = SolidColorBrush(Colors.White)
        title_text.FontSize = 13
        title_text.FontWeight = FontWeights.Medium
        self._title_text = title_text
        title_bar.Child = title_text
        top_panel.Children.Add(title_bar)

        # 搜索框
        search_border = Border()
        search_border.Padding = Thickness(8, 6, 8, 6)
        search_border.Background = SolidColorBrush(Color.FromRgb(250, 250, 250))
        search_border.BorderBrush = SolidColorBrush(Color.FromRgb(220, 220, 220))
        search_border.BorderThickness = Thickness(0, 0, 0, 1)

        self.search_box = TextBox()
        self.search_box.FontSize = 12
        self.search_box.Padding = Thickness(6, 4, 6, 4)
        self.search_box.BorderBrush = SolidColorBrush(Color.FromRgb(180, 180, 180))
        self.search_box.BorderThickness = Thickness(1)

        # 占位符提示（用 Tag 存储）
        self.search_box.Tag = "搜索明细表名称…"
        self.search_box.Foreground = SolidColorBrush(Color.FromRgb(160, 160, 160))
        self.search_box.Text = "搜索明细表名称…"
        self.search_box.GotFocus += self._search_got_focus
        self.search_box.LostFocus += self._search_lost_focus
        self.search_box.TextChanged += self._on_search_changed
        self.search_box.KeyDown += self._on_key_down
        self._placeholder_active = True

        search_border.Child = self.search_box
        top_panel.Children.Add(search_border)
        root.Children.Add(top_panel)

        # ── 底部：状态栏 ──
        status_border = Border()
        status_border.Background = SolidColorBrush(Color.FromRgb(245, 245, 245))
        status_border.BorderBrush = SolidColorBrush(Color.FromRgb(220, 220, 220))
        status_border.BorderThickness = Thickness(0, 1, 0, 0)
        status_border.Padding = Thickness(10, 5, 10, 5)
        DockPanel.SetDock(status_border, Dock.Bottom)
        self._status_text = TextBlock()
        self._status_text.FontSize = 11
        self._status_text.Foreground = SolidColorBrush(Color.FromRgb(130, 130, 130))
        status_border.Child = self._status_text
        root.Children.Add(status_border)

        # ── 中间：明细表列表 ──
        self.list_box = ListBox()
        self.list_box.BorderThickness = Thickness(0)
        self.list_box.Padding = Thickness(4)
        self.list_box.FontSize = 13
        self.list_box.MouseDoubleClick += self._on_item_double_click
        self.list_box.KeyDown += self._on_key_down
        root.Children.Add(self.list_box)

        self.Content = root

    def _populate_list(self, schedules):
        self.list_box.Items.Clear()
        for s in schedules:
            item = ListBoxItem()
            item.Padding = Thickness(8, 5, 8, 5)
            item.Tag = s.Id  # 保存 ElementId

            panel = DockPanel()
            name_text = TextBlock()
            name_text.Text = s.Name
            name_text.TextWrapping = TextWrapping.Wrap
            name_text.VerticalAlignment = VerticalAlignment.Center
            panel.Children.Add(name_text)
            item.Content = panel
            self.list_box.Items.Add(item)

        count = len(schedules)
        total = len(self.all_schedules)
        if count == total:
            self._status_text.Text = "共 {} 张明细表".format(total)
        else:
            self._status_text.Text = "显示 {} / {} 张".format(count, total)

    def _search_got_focus(self, sender, e):
        if self._placeholder_active:
            self.search_box.Text = ""
            self.search_box.Foreground = SolidColorBrush(Colors.Black)
            self._placeholder_active = False

    def _search_lost_focus(self, sender, e):
        if not self.search_box.Text.strip():
            self.search_box.Text = str(self.search_box.Tag)
            self.search_box.Foreground = SolidColorBrush(Color.FromRgb(160, 160, 160))
            self._placeholder_active = True

    def _on_search_changed(self, sender, e):
        if self._placeholder_active:
            return
        keyword = self.search_box.Text.strip().lower()
        if keyword:
            filtered = [s for s in self.all_schedules if keyword in s.Name.lower()]
        else:
            filtered = self.all_schedules
        self._populate_list(filtered)

    def _on_key_down(self, sender, e):
        if e.Key == Key.Enter:
            self._open_selected()
        elif e.Key == Key.Escape:
            self.Close()
        elif e.Key == Key.Down and sender == self.search_box:
            # 焦点移到列表
            if self.list_box.Items.Count > 0:
                self.list_box.Focus()
                self.list_box.SelectedIndex = 0

    def _on_item_double_click(self, sender, e):
        self._open_selected()

    def _open_selected(self):
        item = self.list_box.SelectedItem
        if item and item.Tag:
            try:
                open_schedule(item.Tag)
                self.Close()
            except Exception as ex:
                from System.Windows import MessageBox
                MessageBox.Show("无法打开明细表：\n" + str(ex), "错误")


# ── 入口 ──
win = SheetNavigatorWindow()
win.ShowDialog()
