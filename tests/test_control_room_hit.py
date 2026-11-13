from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from control_room import ControlRoom


class ControlRoomHitTests(unittest.TestCase):
    def test_hollow_border_does_not_cover_navigation_close_or_save(self):
        room=ControlRoom()
        try:
            room.root.update_idletasks()
            for x,y,expected in [(114,346,'伙伴状态'),(510,506,'保存快捷键')]:
                with self.subTest(control=expected):
                    hits=room.c.find_overlapping(x*room.scale,y*room.scale,x*room.scale,y*room.scale)
                    self.assertTrue(hits)
                    top=hits[-1]
                    self.assertEqual(room.c.type(top),'text')
                    self.assertIn(expected,room.c.itemcget(top,'text'))
            close_hits=room.c.find_overlapping(978*room.scale,42*room.scale,978*room.scale,42*room.scale)
            self.assertEqual(room.c.type(close_hits[-1]),'line')
            self.assertTrue(room.c.tag_bind(close_hits[-1],'<Button-1>'))
        finally:
            room.closed=True
            room.root.destroy()
