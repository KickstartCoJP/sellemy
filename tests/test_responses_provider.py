import copy
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import responses_provider as rp
import writer_runtime as writer
import planning_runtime as planner
import test_planning_and_selection as planning_tests
from test_planning_and_selection import candidate
import test_writer_providers as writer_tests
from test_writer_providers import completed
from codex_provider import CodexProviderError
from fixtures import valid_payload
from quality_fixtures import accepted_fixture


def response(value, **changes):
    return io.BytesIO(json.dumps({'status': 'completed', 'output': [
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}],
        **changes}).encode())


class ResponsesTests(unittest.TestCase):
    def test_strict_request_and_separate_credentials(self):
        for stage, schema, value in [('writer', writer.WRITER_JSON_SCHEMA, valid_payload()),
                                     ('planner', planner.PLANNING_SCHEMA, {'candidates': [candidate() for _ in range(6)]})]:
            original = copy.deepcopy(schema)
            env = {'SELLEMY_PLANNER_OPENAI_API_KEY': 'planner-secret',
                   'SELLEMY_WRITER_OPENAI_API_KEY': 'writer-secret',
                   'SELLEMY_OPENAI_PROJECT_ID': 'wrong-project'}
            with patch.dict(os.environ, env, clear=True), patch.object(rp.urllib.request, 'urlopen', return_value=response(value)) as call:
                result, meta = rp.generate(stage, schema, 'prompt')
            self.assertEqual(result, value)
            request = call.call_args.args[0]
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + stage + '-secret')
            self.assertIsNone(request.get_header('Openai-project'))
            body = json.loads(request.data)
            self.assertTrue(body['text']['format']['strict'])
            def check(node):
                if isinstance(node, dict):
                    if node.get('type') == 'object':
                        self.assertFalse(node['additionalProperties'])
                        self.assertEqual(set(node['required']), set(node['properties']))
                    for child in node.values(): check(child)
                elif isinstance(node, list):
                    for child in node: check(child)
            check(body['text']['format']['schema'])
            self.assertEqual(schema, original)

    def test_rejects_bad_outputs_and_transport_errors(self):
        schema = {'type': 'object', 'properties': {'n': {'type': 'number'}}, 'required': ['n']}
        variants = [response({'n': True}), response({'n': 1, 'extra': 1}), response({}),
                    response({'n': 1}, status='incomplete'), response({'n': float('nan')}),
                    io.BytesIO(b'not json'), response({'n': 1}, output=[{'type': 'message', 'content': [{'type': 'refusal'}]}])]
        for result in variants:
            with patch.dict(os.environ, {'SELLEMY_WRITER_OPENAI_API_KEY': 'secret'}), patch.object(rp.urllib.request, 'urlopen', return_value=result):
                with self.assertRaises(rp.ResponsesError): rp.generate('writer', schema, '')
        with patch.dict(os.environ, {'SELLEMY_WRITER_OPENAI_API_KEY': 'secret'}), patch.object(rp.urllib.request, 'urlopen', side_effect=TimeoutError('secret')):
            with self.assertRaisesRegex(rp.ResponsesError, '^Responses transport failed$'):
                rp.generate('writer', schema, '')

    def test_independent_weights_and_boundaries(self):
        env = {'SELLEMY_PLANNER_API_WEIGHT': '100', 'SELLEMY_PLANNER_CODEX_WEIGHT': '0',
               'SELLEMY_WRITER_API_WEIGHT': '0', 'SELLEMY_WRITER_CODEX_WEIGHT': '100'}
        local = lambda: ({}, {'writer_provider_used': 'codex', 'fallback_used': False})
        with patch.dict(os.environ, env, clear=True), patch.object(rp, 'generate', return_value=({}, {'model': 'm'})) as api:
            self.assertEqual(rp.route('planner', {}, '', local)[1]['route_selected'], 'openai_responses')
            self.assertEqual(rp.route('writer', {}, '', local)[1]['route_selected'], 'codex')
            self.assertEqual(api.call_count, 1)
        for draw, expected in [(0.249, 'openai_responses'), (0.25, 'codex')]:
            with patch.dict(os.environ, {'SELLEMY_WRITER_API_WEIGHT': '25', 'SELLEMY_WRITER_CODEX_WEIGHT': '75'}, clear=True), patch.object(rp.random, 'random', return_value=draw), patch.object(rp, 'generate', return_value=({}, {'model': 'm'})):
                self.assertEqual(rp.route('writer', {}, '', local)[1]['route_selected'], expected)
        for weight in ['-1', 'nan', 'inf', 'bad']:
            with patch.dict(os.environ, {'SELLEMY_WRITER_API_WEIGHT': weight}, clear=True):
                with self.assertRaises(ValueError): rp.route('writer', {}, '', local)

    def test_each_stage_api_success_and_fallback_chain(self):
        for stage, module, env, invoke, payload in [
            ('writer', writer, {}, lambda: writer.invoke_writer({}, accepted_fixture()[1]), valid_payload()),
            ('planner', planner, planning_tests.ContinuousPlanningTests()._env(), planner.discover_candidates, {'candidates': [candidate() for _ in range(6)]})]:
            if stage == 'writer':
                fixture = writer_tests.WriterProviderTests(); fixture.setUp(); env = fixture.env
            env = dict(env, **{f'SELLEMY_{stage.upper()}_API_WEIGHT': '100', f'SELLEMY_{stage.upper()}_CODEX_WEIGHT': '0'})
            for fail_api, fail_codex in [(False, False), (True, False), (True, True)]:
                with patch.dict(os.environ, env, clear=True), patch.object(rp, 'generate', side_effect=rp.ResponsesError('safe', kind='transport') if fail_api else None, return_value=(copy.deepcopy(payload), {'model': 'api-model'})), patch.object(module, 'codex_generate_persistent', side_effect=CodexProviderError('limit') if fail_codex else None, return_value=(copy.deepcopy(payload), {})) as codex, patch.object(module.subprocess, 'run', return_value=completed(payload=payload)) as claude, self.assertLogs(rp.LOG, level='WARNING') as logs:
                    result = invoke()
                meta = result[1] if stage == 'writer' else result.provider_metadata
                field = 'writer' if stage == 'writer' else 'planning'
                self.assertEqual(meta[field + '_attempt_count'], 1 + int(fail_api) + int(fail_codex))
                self.assertEqual(codex.call_count, int(fail_api))
                self.assertEqual(claude.call_count, int(fail_codex))
                self.assertIn('provider_used=', '\n'.join(logs.output))
                self.assertEqual(meta['fallback_used'], fail_api)

    def test_sol_output_failure_escalates_once_to_astra(self):
        env = {
            'SELLEMY_WRITER_API_WEIGHT': '100',
            'SELLEMY_WRITER_CODEX_WEIGHT': '0',
            'SELLEMY_WRITER_OPENAI_MODEL': 'gpt-6.1-sol',
            'SELLEMY_WRITER_OPENAI_ESCALATION_MODEL': 'gpt-6-astra',
        }
        calls = []
        def fake_generate(stage, schema, prompt, *, model=None):
            calls.append(model)
            if model == 'gpt-6.1-sol':
                raise rp.ResponsesError('bad structured output', kind='output')
            return {'ok': True}, {'model': model}
        with patch.dict(os.environ, env, clear=True), patch.object(rp, 'generate', side_effect=fake_generate):
            value, meta = rp.route('writer', {}, 'prompt', lambda: self.fail('local fallback should not run'))
        self.assertEqual(value, {'ok': True})
        self.assertEqual(calls, ['gpt-6.1-sol', 'gpt-6-astra'])
        self.assertEqual(meta['writer_model_used'], 'gpt-6-astra')
        self.assertTrue(meta['quality_escalated'])
        self.assertEqual(meta['fallback_reason'], 'sol_output_error')
        self.assertEqual(meta['writer_attempt_count'], 2)

    def test_quality_retry_goes_directly_to_astra(self):
        env = {
            'SELLEMY_WRITER_API_WEIGHT': '100',
            'SELLEMY_WRITER_CODEX_WEIGHT': '0',
            'SELLEMY_WRITER_OPENAI_MODEL': 'gpt-6.1-sol',
            'SELLEMY_WRITER_OPENAI_ESCALATION_MODEL': 'gpt-6-astra',
        }
        with patch.dict(os.environ, env, clear=True), patch.object(
            rp, 'generate', return_value=({'ok': True}, {'model': 'gpt-6-astra'})
        ) as generate:
            _, meta = rp.route(
                'writer', {}, 'prompt', lambda: self.fail('local fallback should not run'),
                escalate=True,
            )
        self.assertEqual(generate.call_args.kwargs['model'], 'gpt-6-astra')
        self.assertEqual(meta['writer_model_used'], 'gpt-6-astra')
        self.assertTrue(meta['quality_escalation_requested'])
        self.assertTrue(meta['quality_escalated'])
        self.assertEqual(meta['writer_attempt_count'], 1)

    def test_api_prompt_embeds_shared_canon_and_brief(self):
        prompt = rp._shared_prompt('writer', 'TASK-CONTENT')
        self.assertIn('CANON_BRIDGE:', prompt)
        self.assertIn('RUNTIME_BRIEF:', prompt)
        self.assertIn('CURRENT_TASK:\nTASK-CONTENT', prompt)
        self.assertIn('Evidence and identity gates are fail-closed', prompt)

    def test_runner_preserves_project_fix_and_loads_stage_files(self):
        script = (Path(__file__).resolve().parents[1] / 'ops/run-growth.sh').read_text()
        self.assertIn('unset SELLEMY_OPENAI_PROJECT_ID', script)
        self.assertNotIn('export SELLEMY_OPENAI_PROJECT_ID=', script)
        self.assertIn('for stage in planner writer', script)
        self.assertIn('source "$STAGE_SECRETS"', script)


if __name__ == '__main__': unittest.main()
