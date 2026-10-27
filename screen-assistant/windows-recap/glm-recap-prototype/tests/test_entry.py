from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from glm_desktop import GLMDesktop
from core import RecapError


class EntryTests(unittest.TestCase):
    def driver(self):
        d=object.__new__(GLMDesktop)
        d.key=Mock(); d.target=Mock(return_value=('view','bookmark'))
        d.click=Mock(); d.confirm_portal=Mock(return_value='confirmed')
        d.type_replacement=Mock(); d.mcp=Mock()
        d.recover_clipped_entry=Mock(side_effect=lambda window: window)
        d.observe=Mock(return_value='view')
        d.portal_url='https://union.bytedance.com/open/portal/index'
        return d

    def test_existing_local_edge_bookmark_preferred(self):
        d=self.driver(); window={'title':'新建标签页 - Microsoft Edge'}
        with patch('glm_desktop.visible_bookmark',return_value='bookmark'):
            self.assertEqual(d.open_entry(window,True),'confirmed')
        d.click.assert_called_once_with('view','bookmark')
        d.type_replacement.assert_not_called()
        self.assertNotIn('ctrl+t',[q.args[1] for q in d.key.call_args_list])

    def test_missing_bookmark_uses_explicit_index(self):
        d=self.driver()
        with patch('glm_desktop.visible_bookmark',side_effect=RecapError('bookmark_missing')):
            self.assertEqual(d.open_entry({'title':'新建标签页'},True),'confirmed')
        d.type_replacement.assert_called_once_with('view','bookmark','https://union.bytedance.com/open/portal/index')
        d.click.assert_not_called()

    def test_ambiguous_bookmark_does_not_fallback(self):
        d=self.driver()
        with patch('glm_desktop.visible_bookmark',side_effect=RecapError('bookmark_ambiguous')):
            with self.assertRaises(RecapError): d.open_entry({'title':'新建标签页'},True)
        d.target.assert_not_called(); d.click.assert_not_called(); d.type_replacement.assert_not_called()

    def test_ambiguous_or_user_control_never_falls_back(self):
        for reason in ('bookmark_ambiguous','USER_CONTROL','control_generation_changed'):
            d=self.driver(); d.observe.side_effect=RecapError(reason)
            with self.assertRaises(RecapError): d.open_entry({'title':'新建标签页'},True)
            d.type_replacement.assert_not_called(); d.click.assert_not_called()

    def test_focus_recovery_discards_observation_once(self):
        d=self.driver(); d.guard=Mock(); d.focus=Mock(return_value={'handle':1})
        del d.observe
        with patch('llm_desktop.LLMDesktop.observe',side_effect=[RecapError('foreground_changed'),'fresh']) as observe:
            self.assertEqual(d.observe({'handle':1}),'fresh')
            self.assertEqual(observe.call_count,2)
        d.guard.assert_called_once_with(); d.focus.assert_called_once()

    def test_repeated_ordinary_focus_changes_reobserve_and_resume(self):
        d=self.driver(); d.guard=Mock(); d.focus=Mock(return_value={'handle':1})
        del d.observe
        with patch('llm_desktop.LLMDesktop.observe',side_effect=[
                RecapError('foreground_changed'),RecapError('foreground_changed'),'fresh']) as observe:
            self.assertEqual(d.observe({'handle':1}),'fresh')
            self.assertEqual(observe.call_count,3)
        self.assertEqual(d.focus.call_count,2)
        self.assertEqual(d.guard.call_count,2)

    def test_focus_recovery_stops_for_user_or_repeated_loss(self):
        d=self.driver(); d.guard=Mock(side_effect=RecapError('USER_CONTROL')); d.focus=Mock()
        del d.observe
        with patch('llm_desktop.LLMDesktop.observe',side_effect=RecapError('foreground_changed')):
            with self.assertRaises(RecapError) as caught: d.observe({'handle':1})
        self.assertEqual(caught.exception.reason,'USER_CONTROL'); d.focus.assert_not_called()
        d.guard=Mock(); d.focus=Mock(return_value={'handle':1})
        with patch('llm_desktop.LLMDesktop.observe',side_effect=RecapError('foreground_changed')) as observe:
            with self.assertRaises(RecapError): d.observe({'handle':1})
            self.assertEqual(observe.call_count,5)

    def test_focus_loss_after_model_discards_target_and_relocates_once(self):
        d=self.driver(); del d.target
        window={'handle':1}; old=Mock(window=window); fresh=Mock(window=window)
        d.guard=Mock(side_effect=[RecapError('foreground_changed'),None,None])
        d.focus=Mock(return_value=window)
        with patch('llm_desktop.LLMDesktop.target',side_effect=[(old,'stale'),(fresh,'new')]) as target:
            self.assertEqual(d.target(window,'goal'),(fresh,'new'))
            self.assertEqual(target.call_count,2)
        d.focus.assert_called_once_with(window)

    def test_user_control_after_model_does_not_refocus(self):
        d=self.driver(); del d.target
        window={'handle':1}; view=Mock(window=window)
        d.guard=Mock(side_effect=RecapError('USER_CONTROL')); d.focus=Mock()
        with patch('llm_desktop.LLMDesktop.target',return_value=(view,'unused')):
            with self.assertRaises(RecapError): d.target(window,'goal')
        d.focus.assert_not_called()

if __name__=='__main__': unittest.main()
