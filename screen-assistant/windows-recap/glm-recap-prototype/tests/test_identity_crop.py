from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from glm_desktop import GLMDesktop
from desktop import View
from core import Token,RecapError


class IdentityCropTests(unittest.TestCase):
    def driver(self,folder):
        image=Path(folder)/'screen.png'; Image.new('RGB',(500,400)).save(image)
        d=object.__new__(GLMDesktop); d.evidence=Path(folder)
        d.save_minimal=Mock(side_effect=RecapError('evidence_region_missing'))
        d.vision=Mock(); d.vision.read.return_value={'account':'demo','live_status':None}
        view=View([Token('demo',(100,150,150,165),.99),Token('demo',(100,175,150,190),.99)],
            image,{'rect':[0,0,500,400]},(500,400),'offline',[])
        return d,view

    def test_bare_account_does_not_trigger_visual_or_save(self):
        with tempfile.TemporaryDirectory() as folder:
            d,view=self.driver(folder)
            with self.assertRaises(RecapError) as error: d.identity(view,'demo')
            self.assertEqual(error.exception.reason,'wrong_identity')
            d.vision.read.assert_not_called()
            self.assertFalse((d.evidence/'identity-check.png').exists())

    def test_disjoint_or_missing_account_regions_do_not_call_model(self):
        with tempfile.TemporaryDirectory() as folder:
            for tokens in ([],[Token('demo',(100,10,150,25),.99),Token('demo',(100,350,150,365),.99)]):
                d,view=self.driver(folder); view.tokens=tokens
                with self.assertRaises(RecapError): d.identity(view,'demo')
                d.vision.read.assert_not_called()

if __name__=='__main__': unittest.main()
