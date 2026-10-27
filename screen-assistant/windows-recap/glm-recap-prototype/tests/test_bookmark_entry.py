from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[2]/'fixed-recap-prototype')]
from core import Token, RecapError
from desktop import View
from glm_desktop import visible_bookmark,GLMDesktop


class BookmarkEntryTests(unittest.TestCase):
    def setUp(self):
        self.anchor=Token('导入收藏夹',(84,88,148,103),.996)
        self.bookmark=Token('直播服务平台·机构...',(818,87,934,104),.968)
        self.window={'rect':[633,73,1667,833]}
        self.view=View([self.anchor,self.bookmark],Path('unused'),self.window,(1034,760),'test',[])

    def test_observed_text_geometry_and_screen_mapping(self):
        found=visible_bookmark(self.view)
        self.assertIs(found,self.bookmark)
        self.assertEqual(self.view.point(found),[1509,168])

    def test_scaled_and_translated_window_mapping(self):
        self.view.window={'rect':[-200,100,1868,1620]}
        self.assertEqual(self.view.point(visible_bookmark(self.view)),[1552,291])

    def test_missing_anchor_or_target_low_score_stops(self):
        for tokens in ([self.bookmark],[self.anchor],
                       [self.anchor,Token('直播服务平台·机构',(818,87,934,104),.5)]):
            with self.subTest(tokens=tokens):
                self.view.tokens=tokens
                with self.assertRaises(RecapError): visible_bookmark(self.view)

    def test_ambiguous_same_row_stops(self):
        self.view.tokens.append(Token('直播服务平台·机构版',(700,88,800,104),.99))
        with self.assertRaisesRegex(RecapError,'bookmark_ambiguous'): visible_bookmark(self.view)

    def test_page_text_is_not_a_bookmark(self):
        self.view.tokens=[self.anchor,Token('直播服务平台·机构',(818,125,934,140),.99)]
        with self.assertRaisesRegex(RecapError,'bookmark_missing'): visible_bookmark(self.view)

    def test_unique_anchor_ignores_other_rows(self):
        self.view.tokens.append(Token('直播服务平台·机构',(700,160,900,180),.99))
        self.assertIs(visible_bookmark(self.view),self.bookmark)

    def test_control_or_focus_failure_prevents_click(self):
        d=object.__new__(GLMDesktop); d.recover_clipped_entry=Mock(side_effect=lambda w:w)
        d.key=Mock(); d.click=Mock(); d.observe=Mock(side_effect=RecapError('USER_CONTROL')); d.mcp=Mock()
        with self.assertRaisesRegex(RecapError,'USER_CONTROL'): d.open_entry({'title':'新建标签页'},True)
        d.click.assert_not_called()

if __name__=='__main__': unittest.main()
