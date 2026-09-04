"""Check the archive using only Python's standard library; never call an API."""
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    files = [p for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts
             and '__pycache__' not in p.parts]
    patterns = [r'\b[0-9a-fA-F]{32}\.[A-Za-z0-9_-]{12,}\b', r'\bgh[pousr]_[A-Za-z0-9]{20,}\b',
                r'\bgithub_pat_[A-Za-z0-9_]{20,}\b', r'\bsk-[A-Za-z0-9_-]{20,}\b',
                r'(?i)Bearer\s+[A-Za-z0-9_.-]{20,}']
    json_count = jsonl_rows = 0
    for path in files:
        text = path.read_text(encoding='utf-8-sig')
        assert not any(re.search(p, text) for p in patterns), f'Credential-pattern match: {path.relative_to(root)}'
        if path.suffix == '.json':
            json.loads(text)
            json_count += 1
        elif path.suffix == '.jsonl':
            for line in text.splitlines():
                if line.strip():
                    json.loads(line)
                    jsonl_rows += 1
        elif path.suffix == '.md':
            for url in re.findall(r'(?<!!)\[[^\]]+\]\(([^)]+)\)', text):
                if '://' in url or url.startswith(('#', 'mailto:')) or '<' in url:
                    continue
                assert (path.parent / url.split('#')[0]).exists(), f'Broken link: {path.relative_to(root)} -> {url}'
    with (root / 'MANIFEST.csv').open(encoding='utf-8') as f:
        for row in csv.DictReader(f):
            assert (root / row['archive_path']).stat().st_size == int(row['bytes']), row['archive_path']
    result = {}
    for phase, expected_actions, expected_requests, expected_responses, expected_errors in [
            ('phase1', 695, 581, 547, 34), ('phase2', 716, 925, 843, 82)]:
        directory = root / 'experiments' / phase
        summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
        assert len(summary['slots']) == 8 and all(s['status'] == 'completed' for s in summary['slots'])
        actions = 0
        event_types = Counter()
        scores = {}
        for slot in summary['slots']:
            run = directory / slot['slot']
            game = json.loads((run / 'benchmark.json').read_text(encoding='utf-8'))['game_runs'][0]
            actions += len(game['history'])
            n = game['number_of_levels']
            completed = game['levels_completed']
            efficiency = sum((i + 1) * min(1.15, (game['base_actions_per_level'][i] / game['actions_per_level'][i]) ** 2)
                             for i in range(completed))
            score = 100 * min(efficiency, sum(range(1, completed + 1))) / sum(range(1, n + 1))
            assert math.isclose(score, slot['score_cap_1_15'], abs_tol=1e-9), slot['slot']
            assert slot['actions'] == len(game['history']), slot['slot']
            assert slot['elapsed_seconds'] <= 1800
            assert completed < n
            scores[slot['slot']] = round(score, 8)
            for path in (run / 'artifacts').glob('*usage.jsonl'):
                for line in path.read_text(encoding='utf-8').splitlines():
                    if line.strip():
                        event_types[json.loads(line)['event']] += 1
        assert actions == expected_actions, (phase, actions)
        assert event_types['request'] == expected_requests, dict(event_types)
        assert event_types['response'] == expected_responses, dict(event_types)
        assert event_types['error'] == expected_errors, dict(event_types)
        result[phase] = {'slots': 8, 'actions': actions, 'request_events': dict(event_types), 'scores': scores}
    print(json.dumps({'status': 'PASS', 'files': len(files), 'json_files': json_count,
                      'jsonl_rows': jsonl_rows, 'phases': result}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
