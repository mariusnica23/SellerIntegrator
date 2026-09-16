from pathlib import Path
from copy import deepcopy
import unittest
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from integrator.mapping import load_mapping
from support import workspace_temp

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


class MappingTests(unittest.TestCase):
    def fixture(self, folder, numeric=False, duplicate=False, formula=False, codes_only=False, numeric_fgo=False):
        source = Path(__file__).resolve().parent.parent/'assets'/('Model_mapare_coduri.xlsx' if codes_only else 'Model_mapare.xlsx')
        target = folder/'map.xlsx'
        with ZipFile(source) as zin, ZipFile(target, 'w') as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    root = ET.fromstring(data)
                    body = root.find(NS+'sheetData')
                    row = body.find(NS+"row[@r='2']")
                    if row is None:
                        row = ET.SubElement(body, NS+'row', {'r':'2'})
                    for cell in list(row):
                        if cell.get('r','')[0] in 'ABCDE':
                            row.remove(cell)
                    values = [('A','0001234567890'), ('B','0001')] if codes_only else [('A','0001234567890'), ('B','Produs cu diacritice: șurub'), ('C','BUC'), ('D','0001'), ('E','21')]
                    for col, text in values:
                        cell = ET.SubElement(row, NS+'c', {'r':col+'2','t':'inlineStr'})
                        if (col=='A' and numeric) or (col=='B' and numeric_fgo):
                            cell.set('t','n')
                            ET.SubElement(cell,NS+'v').text='1234567890'
                        elif col=='B' and formula:
                            cell.attrib.pop('t')
                            ET.SubElement(cell,NS+'f').text='1+1'
                        else:
                            ET.SubElement(ET.SubElement(cell,NS+'is'),NS+'t').text=text
                    if duplicate:
                        existing = body.find(NS+"row[@r='3']")
                        if existing is not None:
                            body.remove(existing)
                        dup=deepcopy(row)
                        dup.set('r','3')
                        for cell in dup:
                            cell.set('r',cell.get('r')[:-1]+'3')
                        body.append(dup)
                    body[:] = sorted(body, key=lambda row:int(row.get('r')))
                    data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
                zout.writestr(item,data)
        return target

    def test_template_import_preserves_identifiers_and_ignores_notes(self):
        with workspace_temp() as folder:
            result=load_mapping(self.fixture(folder))
            self.assertEqual(list(result),['0001234567890'])
            self.assertEqual(result['0001234567890'].fgo_code,'0001')

    def test_two_code_columns_need_no_name_or_vat(self):
        with workspace_temp() as folder:
            result = load_mapping(self.fixture(folder, codes_only=True))
            product = result['0001234567890']
            self.assertEqual(product.fgo_code, '0001')
            self.assertEqual(product.name, '')
            self.assertFalse(product.fgo_verified)

    def test_numeric_fgo_code_is_rejected_to_preserve_leading_zeroes(self):
        with workspace_temp() as folder, self.assertRaisesRegex(ValueError, 'cod_fgo.*TEXT'):
            load_mapping(self.fixture(folder, codes_only=True, numeric_fgo=True))

    def test_duplicate_barcode_is_rejected(self):
        with workspace_temp() as folder, self.assertRaisesRegex(ValueError,'duplicat'):
            load_mapping(self.fixture(folder,duplicate=True))

    def test_numeric_barcode_is_rejected(self):
        with workspace_temp() as folder, self.assertRaisesRegex(ValueError,'TEXT'):
            load_mapping(self.fixture(folder,numeric=True))

    def test_formula_is_rejected(self):
        with workspace_temp() as folder, self.assertRaisesRegex(ValueError,'formulele'):
            load_mapping(self.fixture(folder,formula=True))
