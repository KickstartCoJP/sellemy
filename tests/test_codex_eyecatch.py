from __future__ import annotations
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import codex_eyecatch


def fake_png(width=1536, height=1024):
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', width, height) + b'\x08\x06\x00\x00\x00' + b'0000'


class CodexEyecatchTests(unittest.TestCase):
    def test_prompt_requires_exact_built_in_generation_route_literal(self):
        prompt = codex_eyecatch._prompt(
            slug='sample', title='Sample', category='gadget', evidence={},
            payload={'summary':'summary','products':[]}, output=Path('/tmp'))
        self.assertIn('generation_route MUST be the exact literal "built-in_image_gen"', prompt)
        self.assertIn('Do not return "codex_cli_imagegen" there', prompt)

    def test_designer_writes_verified_receipt_and_token_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def generate(_command, _model, _schema, prompt, **kwargs):
                out = root / 'generated_images/thread/generated.png'
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(fake_png())
                self.assertIn('built-in image generation', prompt)
                return ({'status':'completed','output_path':str(out),'generation_route':'built-in_image_gen',
                         'visual_family':'Lifestyle Scene','summary':'ok'},
                        {'session_id':'thread','member_binding_revision':5,'input_tokens':120,
                         'cached_input_tokens':40,'cache_write_input_tokens':0,'uncached_input_tokens':80,
                         'output_tokens':30,'reasoning_output_tokens':10,'total_tokens':150})
            with patch.object(codex_eyecatch, 'ROOT', root), \
                 patch.object(codex_eyecatch, '_command', return_value=('/bin/codex',)), \
                 patch.object(codex_eyecatch, '_model', return_value='model'), \
                 patch.object(codex_eyecatch, '_generated_root', return_value=(root/'generated_images').resolve()), \
                 patch.object(codex_eyecatch, 'generate_persistent', side_effect=generate):
                receipt = codex_eyecatch.ensure_codex_eyecatch(
                    slug='sample', title='sample title', category='dailygoods', evidence={}, payload={'summary':'summary','products':[]})
            self.assertEqual(receipt['generation_method'], 'codex_cli_imagegen')
            self.assertEqual(receipt['role_id'], 'bu-codex-sellemy-designer')
            self.assertEqual(receipt['token_usage']['input_tokens'], 120)
            self.assertEqual(receipt['token_usage']['total_tokens'], 150)
            self.assertEqual(receipt['token_usage_source'], 'codex_rollout.turn_token_usage')
            self.assertFalse(receipt['image_generation_internal_usage_exposed'])
            self.assertEqual((receipt['width'], receipt['height']), (1536, 1024))
            self.assertTrue((root/'img/sample/sample.png').is_file())
            stored=json.loads((root/'data/eyecatch-receipts/sample.json').read_text())
            self.assertEqual(stored['image_sha256'], receipt['image_sha256'])

    def test_wrong_dimensions_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def generate(*_args, **_kwargs):
                out=root/'generated_images/thread/generated.png'; out.parent.mkdir(parents=True,exist_ok=True)
                out.write_bytes(fake_png(1024,1024))
                return ({'status':'completed','output_path':str(out),'generation_route':'built-in_image_gen','visual_family':'Lifestyle Scene','summary':'bad'}, {'session_id':'thread','member_binding_revision':5})
            with patch.object(codex_eyecatch,'ROOT',root), patch.object(codex_eyecatch,'_command',return_value=('/bin/codex',)), patch.object(codex_eyecatch,'_generated_root',return_value=(root/'generated_images').resolve()), patch.object(codex_eyecatch,'generate_persistent',side_effect=generate):
                with self.assertRaisesRegex(codex_eyecatch.CodexEyecatchError, 'dimensions invalid'):
                    codex_eyecatch.ensure_codex_eyecatch(slug='sample',title='x',category='dailygoods',evidence={},payload={})

if __name__ == '__main__': unittest.main()
