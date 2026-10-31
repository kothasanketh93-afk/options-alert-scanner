import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config


class T(unittest.TestCase):
    def test_pdhl_is_index_only(self):
        self.assertTrue(set(config.PDHL_SYMBOLS) <= {"NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX", "FINNIFTY"})
        self.assertTrue(set(config.ORB_PIVOT_SYMBOLS) <= set(config.PDHL_SYMBOLS))
