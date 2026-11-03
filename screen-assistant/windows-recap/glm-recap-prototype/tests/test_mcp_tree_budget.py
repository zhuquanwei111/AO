import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'windows-mcp'))
sys.path.insert(0, str(ROOT / 'fixed-recap-prototype'))

from core import RecapError
from desktop import address_seed_candidates
try:
    from windows_mcp.tree.budget import TreeElementBudget, resolve_max_tree_elements
    from windows_mcp.tree.views import Center, SemanticNode, TreeState
    spec = importlib.util.spec_from_file_location('recap_mcp_client', ROOT / 'windows-mcp' / 'mcp_stdio_client.py')
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
except ModuleNotFoundError:
    client = None


@unittest.skipIf(client is None, '需要专用 Windows-MCP 运行环境')
class TreeBudgetTests(unittest.TestCase):
    def test_only_service_process_gets_bounded_limit(self):
        params = client.server_parameters(sys.executable)
        self.assertEqual(params.env['WINDOWS_MCP_MAX_TREE_ELEMENTS'], '2000')
        self.assertLessEqual(int(params.env['WINDOWS_MCP_MAX_TREE_ELEMENTS']), 4 * resolve_max_tree_elements({}))

    def test_official_budget_and_renderer_preserve_truncation_guard(self):
        root = SemanticNode('desktop', 'desktop')
        window = SemanticNode('window', 'window', name='Synthetic')
        root.add_child(window)
        for index in range(501):
            window.add_child(SemanticNode('button', 'interactive', name='sample', center=Center(index, 80)))
        window.add_child(SemanticNode('Edit', 'interactive', name='Address and search bar', center=Center(200, 40)))
        budget = TreeElementBudget(500)
        self.assertFalse(budget.try_consume(502))
        truncated = 'UI Tree:\n' + TreeState(semantic_tree_root=root, truncated=budget.truncated, element_limit=500).semantic_tree_to_string()
        with self.assertRaises(RecapError):
            address_seed_candidates(truncated, 'Synthetic', [0, 0, 1000, 600])
        budget = TreeElementBudget(2000)
        self.assertTrue(budget.try_consume(502))
        complete = 'UI Tree:\n' + TreeState(semantic_tree_root=root, truncated=budget.truncated, element_limit=2000).semantic_tree_to_string()
        self.assertEqual(len(address_seed_candidates(complete, 'Synthetic', [0, 0, 1000, 600])), 1)

    def test_final_interpreter_reads_limit_and_renders_bounded_capture(self):
        script = r'''import sys
sys.path.insert(0, sys.argv[1])
from windows_mcp.tree.budget import TreeElementBudget, resolve_max_tree_elements
from windows_mcp.tree.views import Center, SemanticNode, TreeState
from core import RecapError
from desktop import address_seed_candidates
limit = resolve_max_tree_elements()
budget = TreeElementBudget(limit)
root = SemanticNode('desktop', 'desktop')
window = SemanticNode('window', 'window', name='Synthetic')
root.add_child(window)
for index in range(501):
    if not budget.try_consume(): break
    window.add_child(SemanticNode('button', 'interactive', name='sample', center=Center(index,80)))
if budget.try_consume():
    window.add_child(SemanticNode('Edit', 'interactive', name='Address and search bar', center=Center(200,40)))
tree = 'UI Tree:\n' + TreeState(semantic_tree_root=root, truncated=budget.truncated, element_limit=limit).semantic_tree_to_string()
try: count = len(address_seed_candidates(tree, 'Synthetic', [0,0,1000,600]))
except RecapError: count = -1
print(f'{limit} {int(budget.truncated)} {count}')
'''
        params = client.server_parameters(sys.executable)
        env = os.environ.copy()
        env.pop('WINDOWS_MCP_MAX_TREE_ELEMENTS', None)
        command = [params.command, '-I', '-B', '-c', script, str(ROOT / 'fixed-recap-prototype')]
        default = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        env.update(params.env)
        configured = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(default.stdout.strip(), '500 1 -1')
        self.assertEqual(configured.stdout.strip(), '2000 0 1')


if __name__ == '__main__':
    unittest.main()
