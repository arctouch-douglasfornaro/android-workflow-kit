"""Delta review evidence is available and cannot be silently discarded."""
import json
import unittest
from unittest import mock

import test_review as fixture_helpers
from android_workflow import review
from android_workflow.cli import log_stage, read_json


class DeltaEvidenceIntegration(unittest.TestCase):
    def test_archive_delta_inputs_and_draft_guard(self):
        fixture = fixture_helpers.FlowTests()
        first_review = {'status': 'approved', 'blocking': [], 'concerns': [], 'suggestions': [],
                        'acceptance': [{'id': 'AC1', 'status': 'met', 'evidence': 'payload test'}]}
        with fixture_helpers.Project(fixture_helpers.FlowTests.CHANGE) as project:
            worktree = fixture.run_to_draft(project, first_review)
            run = review.agent_dir_for(worktree)
            prior_review = (run / 'review.json').read_bytes()
            prior_spec = (run / review.SPEC_FILE).read_bytes()
            state_path = run / review.STATE_FILE
            state = read_json(state_path)
            state['posted'] = [{'head': project.head, 'draft_id': 'x', 'event': 'APPROVE'}]
            state_path.write_text(json.dumps(state))
            new_head = fixture_helpers.push_fix(project)
            result = project.start(fixture_helpers.fake_pr(new_head))
            self.assertEqual(result['round'], 2)
            self.assertEqual((run / f'review.json.{project.head}').read_bytes(), prior_review)
            self.assertEqual((run / f'{review.SPEC_FILE}.{project.head}').read_bytes(), prior_spec)
            self.assertFalse((run / 'review.json').exists())
            checks = review.next_action(worktree)
            reviewer = next(item for item in checks['run'] if item['agent'] == 'aw-reviewer')
            self.assertEqual(reviewer['mode'], 'pr-delta')
            self.assertIn(f'RUN/review.json.{project.head}', reviewer['read'])
            self.assertIn(f'RUN/{review.SPEC_FILE}.{project.head}', reviewer['read'])
            second_review = dict(first_review, acceptance=[{'id': 'AC1', 'status': 'unverified'}])
            (run / 'review.json').write_text(json.dumps(second_review))
            log_stage(worktree, 'Reviewer', 'completed', 'delta', 'host')
            with mock.patch.object(review, 'fetch_pr', return_value=fixture_helpers.fake_pr(new_head)):
                review.ci(worktree)
            answer = review.next_action(worktree)
            # Not a draft that refuses forever: the Reviewer is resumed to explain the lost proof.
            self.assertEqual(answer['next'], 'checks')
            resumed = next(item for item in answer['run'] if item['agent'] == 'aw-reviewer')
            self.assertEqual((resumed['resume'], resumed['acceptance_regressions']), (True, ['AC1']))
            self.assertFalse(any(row.get('quiet') for row in read_json(state_path)['posted']))
            with self.assertRaisesRegex(ValueError, 'acceptance lost prior proof.*AC1'):
                review.draft(worktree)
            # Back again with no reason: asked once, then the developer decides.
            log_stage(worktree, 'Reviewer', 'completed', 'delta', 'host')
            self.assertEqual(review.next_action(worktree)['next'], 'ask_user')
            second_review['acceptance'][0]['regression_reason'] = 'The changed code invalidates the earlier test.'
            (run / 'review.json').write_text(json.dumps(second_review))
            self.assertNotIn(review.next_action(worktree)['next'], {'ask_user', 'checks'})
            self.assertEqual(review.draft(worktree)['status'], 'drafted')

    def test_new_commits_seen_by_ci_start_a_new_round_instead_of_looping(self):
        fixture = fixture_helpers.FlowTests()
        approved = {'status': 'approved', 'blocking': [], 'concerns': [], 'suggestions': [],
                    'acceptance': [{'id': 'AC1', 'status': 'met', 'evidence': 'payload test'}]}
        with fixture_helpers.Project(fixture_helpers.FlowTests.CHANGE) as project:
            worktree = fixture.run_to_draft(project, approved)
            run = review.agent_dir_for(worktree)
            (run / review.DRAFT_FILE).unlink(missing_ok=True)
            (run / review.CI_FILE).write_text(json.dumps({'status': 'head_moved', 'head': 'f' * 40}))
            answer = review.next_action(worktree)
            self.assertEqual(answer['next'], 'start')
            self.assertIn('CLI review start', answer['command'])


class TeamFixRound(unittest.TestCase):
    def test_a_team_fix_round_is_recorded_so_an_unchanged_one_ends(self):
        import tempfile
        from pathlib import Path
        from android_workflow import decide

        team = {'slices': [{'id': 'a', 'files': ['A.kt']}, {'id': 'b', 'files': ['B.kt']}]}
        blocking = {'status': 'changes_requested', 'blocking': [{'id': 'R1', 'file': 'A.kt'}]}
        answer = decide._fix_round(Path('.'), team, ['R1'], blocking, 'review found problems', 'fp1', [])
        self.assertEqual((answer['next'], answer['fix_of'], answer['ids']), ('fix', 'fp1', ['R1']))
        with tempfile.TemporaryDirectory() as tmp:
            # The line `_record` writes for this answer (it keeps fix_of and ids).
            line = {'at': 1, **{key: answer[key] for key in ('next', 'fix_of', 'ids')}}
            (Path(tmp) / decide.DECISIONS_FILE).write_text(json.dumps(line) + '\n')
            self.assertTrue(decide._fixed_before(Path(tmp), 'fp1'))


if __name__ == '__main__':
    unittest.main()
