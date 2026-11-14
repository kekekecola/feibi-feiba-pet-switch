from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import control_room as ui


class ControlRoomResizeTests(unittest.TestCase):
    def test_all_edges_and_corners_have_handles(self):
        for point, edge in [((1,1),'nw'),((500,1),'n'),((1039,1),'ne'),
                            ((1,300),'w'),((1039,300),'e'),((1,699),'sw'),
                            ((500,699),'s'),((1039,699),'se'),((978,42),'')]:
            self.assertEqual(ui.resize_edge(*point,1040,700),edge)

    def test_resize_keeps_aspect_and_opposite_anchor(self):
        original=(100,200,1040,700)
        for edge in ('nw','n','ne','w','e','sw','s','se'):
            with self.subTest(edge=edge):
                x,y,w,h=ui.resized_bounds(original,edge,208,140)
                self.assertAlmostEqual(w/h,1040/700,places=2)
                if 'w' in edge:self.assertEqual(x+w,1140)
                if 'e' in edge:self.assertEqual(x,100)
                if 'n' in edge:self.assertEqual(y+h,900)
                if 's' in edge:self.assertEqual(y,200)

    def test_minimum_and_negative_monitor_coordinates(self):
        self.assertEqual(ui.resized_bounds((-1040,-700,1040,700),'se',-500,-500),(-1040,-700,832,560))
        self.assertEqual(ui.resized_bounds((0,0,1040,700),'se',10000,10000)[2:],(3120,2100))

    def test_real_canvas_resizes_without_losing_unsaved_hotkey_or_hit_targets(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui,'DATA',Path(folder)):
            room=ui.ControlRoom()
            try:
                room.root.deiconify()
                room.root.update_idletasks()
                room.entry.delete(0,'end');room.entry.insert(0,'Ctrl+Alt+J')
                entry=room.entry
                entry.selection_range(2,6)
                canvas_items=room.c.find_all()
                room.root.geometry('1248x840')
                room.root.update_idletasks();room.redraw_size()
                self.assertIs(room.entry,entry)
                self.assertEqual(room.c.find_all(),canvas_items)
                self.assertEqual((entry.index('sel.first'),entry.index('sel.last')),(2,6))
                self.assertEqual(room.entry.get(),'Ctrl+Alt+J')
                self.assertEqual((room.bg.width(),room.bg.height()),(1248,840))
                x,y=510*room.scale,506*room.scale
                top=room.c.find_overlapping(x,y,x,y)[-1]
                self.assertIn('保存快捷键',room.c.itemcget(top,'text'))
                room.resizing=('se',0,0,(0,0,1040,700))
                room.drag_end()
                self.assertTrue((Path(folder)/'control-room-window.json').exists())
                self.assertIsNone(room.c.grab_current())
            finally:
                room.closed=True;room.root.destroy()

    def test_live_frame_prepares_content_before_publishing_window_bounds(self):
        room=ui.ControlRoom.__new__(ui.ControlRoom)
        room.root=Mock();room.resize_after='timer';room.pending_bounds=(100,200,1248,840);room.closed=False
        order=[]
        room.resize_content=Mock(side_effect=lambda *a,**k:order.append('content'))
        room.root.geometry.side_effect=lambda *a:order.append('window')
        room.apply_pending_resize()
        self.assertEqual(order,['content','window'])
        room.resize_content.assert_called_once_with(1248,840,fast=True)
        self.assertIsNone(room.pending_bounds)
        self.assertIsNone(room.resize_after)

    def test_many_pointer_samples_produce_one_frame_using_latest_target(self):
        room=ui.ControlRoom.__new__(ui.ControlRoom)
        room.root=Mock();room.root.after.return_value='timer'
        room.resizing=('se',0,0,(0,0,1040,700));room.resize_after=None
        room.minimum_width=832;room.last_resize_frame=0
        for x,y in [(10,7),(20,14),(208,140)]:
            room.drag_move(SimpleNamespace(x_root=x,y_root=y))
        room.root.after.assert_called_once()
        room.root.geometry.assert_not_called()
        self.assertEqual(room.pending_bounds,(0,0,1248,840))

    def test_diagnostic_widget_and_report_survive_live_resize(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(ui,'DATA',Path(folder)), \
             patch.object(ui.diagnostics,'collect',return_value={'report':'line\n'*100}) as collect:
            room=ui.ControlRoom('diagnostics')
            try:
                room.root.deiconify();room.root.update_idletasks();room.open_detail()
                box=room.children[0]
                room.resize_content(1248,840,fast=True)
                self.assertIs(room.children[0],box)
                collect.assert_called_once()
                self.assertIn('line',box.get('1.0','end'))
            finally:
                room.closed=True;room.root.destroy()
