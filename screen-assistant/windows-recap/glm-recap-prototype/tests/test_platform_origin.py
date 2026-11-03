"""平台归属仅按URL主机判断，登录、账号与场次仍独立核对。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'fixed-recap-prototype'))
from core import RecapError,Token
from desktop import Desktop,portal_page_evidence
from glm_desktop import GLMDesktop
from glm_vision import GLMVision
import test_memory_visual as fixtures


def token(text):
    return Token(text,(10,10,750,30))


class PlatformOriginTests(unittest.TestCase):
    def test_same_host_any_path_needs_no_brand_or_navigation(self):
        for path in ('','/','/open/portal/index','/anchor/detail/123','/different/page?x=1#tab','/login'):
            with self.subTest(path=path):
                self.assertTrue(all(portal_page_evidence([token('https://union.bytedance.com'+path)]).values()))
        # 沿用浏览器地址栏省略HTTPS的现有OCR展示形式。
        self.assertTrue(all(portal_page_evidence([token('union.bytedance.com/anchor/detail/123')]).values()))
        self.assertTrue(all(portal_page_evidence([token('HTTPS://UNION.BYTEDANCE.COM:443/any')]).values()))

    def test_other_hosts_or_embedded_urls_are_not_platform(self):
        for url in ('https://union.bytedance.com.evil.test/open/portal',
                    'https://other.bytedance.com/open/portal',
                    'https://union.bytedance.com@evil.test/open/portal',
                    'https://evil.test/?next=https://union.bytedance.com/open/portal',
                    'https://evilunion.bytedance.com/open/portal',
                    'https://evil.test\\@union.bytedance.com/open/portal',
                    'https://[invalid',
                    'http://union.bytedance.com/open/portal',
                    '页面文字 https://union.bytedance.com/open/portal'):
            with self.subTest(url=url):
                self.assertFalse(portal_page_evidence([token(url)])['portal_origin_visible'])

    def test_url_already_visible_does_not_require_model_content_confirmation(self):
        view=fixtures.MemoryVisualTests().view([token('https://union.bytedance.com/any/page')])
        driver=object.__new__(GLMDesktop); driver.observe=Mock(return_value=view); driver.vision=Mock()
        self.assertIs(driver.wait_entry(view.window),view)
        self.assertIs(driver.confirm_portal(view.window),view)
        driver.vision.verify_portal.assert_not_called()

    def test_current_platform_page_continues_without_returning_home(self):
        view=fixtures.MemoryVisualTests().view([token('https://union.bytedance.com/any/page')])
        view.window['title']='直播服务平台'
        driver=object.__new__(GLMDesktop); driver.guard=Mock(); driver.mcp=Mock(); driver.probe=Mock()
        driver.probe.windows.return_value=[view.window]; driver.focus=Mock(return_value=view.window)
        driver.observe=Mock(return_value=view); driver.vision=Mock(); driver.key=Mock(); driver.type_replacement=Mock()
        driver.target=Mock(side_effect=RecapError('fixture_following_navigation'))
        with self.assertRaisesRegex(RecapError,'fixture_following_navigation'):
            driver.bind('123456')
        driver.target.assert_called_once_with(view.window,'侧边导航主播管理的展开箭头')
        driver.key.assert_not_called(); driver.type_replacement.assert_not_called()
        driver.vision.verify_portal.assert_not_called()

    def test_existing_visual_url_reader_checks_only_url(self):
        runtime=Mock(); vision=GLMVision(runtime); view=fixtures.MemoryVisualTests().view([])
        for path in ('/open/portal/index','/anchor/detail/123','/anything/else'):
            runtime.run_crop.return_value={'origin':'https://union.bytedance.com'+path}
            vision.verify_portal(view,lambda w:w)
        runtime.run_crop.return_value={'origin':'https://union.bytedance.com.evil.test/open/portal'}
        with self.assertRaisesRegex(RecapError,'portal_origin_unconfirmed'):
            vision.verify_portal(view,lambda w:w)

    def test_same_host_login_is_platform_but_authentication_still_stops(self):
        view=fixtures.MemoryVisualTests().view([token('https://union.bytedance.com/login'),Token('验证码',(20,100,100,120))])
        self.assertTrue(all(portal_page_evidence(view.tokens).values()))
        runtime=Mock(); vision=GLMVision(runtime)
        with self.assertRaisesRegex(RecapError,'needs_login_or_verification'):
            vision.verify_portal(view,lambda w:w)
        runtime.run_crop.assert_not_called()

    def test_platform_membership_does_not_accept_wrong_account(self):
        view=fixtures.MemoryVisualTests().view([Token('抖音号:123456',(100,80,230,100))])
        self.assertTrue(all(portal_page_evidence([token('https://union.bytedance.com/profile')]).values()))
        runtime=Mock(); runtime.run_crop.return_value={'account':'654321','live_status':None}
        with self.assertRaisesRegex(RecapError,'wrong_identity'):
            GLMVision(runtime).verify_identity(view,'123456',lambda w:w)

    def test_platform_membership_does_not_accept_other_room(self):
        view=fixtures.MemoryVisualTests().view([Token('抖音号:123456',(100,80,230,100)),
            token('https://union.bytedance.com/open/portal/live_flow/followRoomDetail?roomID=1234567890123456789')])
        self.assertTrue(all(portal_page_evidence(view.tokens).values()))
        driver=object.__new__(Desktop); driver.focus=Mock(return_value=view.window); driver.observe=Mock(return_value=view)
        with self.assertRaisesRegex(RecapError,'room_changed'):
            driver.capture({'window':view.window,'douyin_account':'123456','room_id':'9876543210987654321'})


if __name__=='__main__': unittest.main()
