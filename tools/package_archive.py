"""Build the reading archive from an existing local experiment checkout. No network."""
import argparse
import csv
import difflib
import json
import re
from pathlib import Path
from urllib.parse import unquote


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--background', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    target = Path(__file__).resolve().parents[1]
    pairs = []

    def select(src, dest):
        if not src.is_file():
            raise FileNotFoundError(src)
        pairs.append((src, target / dest))

    runs = [('phase1', 'verified-ls20-20260904'), ('phase2', 'prediction-ls20-20260904')]
    for phase, name in runs:
        root = source / 'local' / name
        for name2 in ['analysis.md', 'analysis.json', 'summary.json', 'summary.csv', 'report.md']:
            select(root / name2, f'experiments/{phase}/{name2}')
        for slot in sorted(root.glob('r*-*')):
            if not slot.is_dir():
                continue
            for name2 in ['benchmark.json', 'run_config.json']:
                select(slot / name2, f'experiments/{phase}/{slot.name}/{name2}')
            for artifact in sorted((slot / 'artifacts').glob('*.jsonl')):
                if artifact.name.endswith('events.jsonl') or artifact.name.endswith('usage.jsonl'):
                    select(artifact, f'experiments/{phase}/{slot.name}/artifacts/{artifact.name}')
        frozen = root / 'source_snapshot'
        for file in sorted(frozen.rglob('*')):
            if file.is_file():
                select(file, Path('code') / f'frozen-{phase}' / file.relative_to(frozen))

    historical = source / 'local/runs-glm53-flash/20260902_130505_glm53-flash-20260902-130458'
    for name in ['run_config.json', 'benchmark.json']:
        select(historical / name, f'experiments/historical/{name}')
    for file in (historical / 'artifacts').glob('*events.jsonl'):
        select(file, f'experiments/historical/artifacts/{file.name}')
    for old, new in [('DUCK_HARNESS_RESULTS_REPORT.md', 'historical'),
                     ('DUCK_V2_IMPLEMENTATION_REPORT.md', 'phase1'),
                     ('DUCK_PREDICTION_IMPLEMENTATION_REPORT.md', 'phase2')]:
        select(source / old, f'reports/{new}.md')
    for name in ['VERIFIED_EXPERIMENT.md', 'PREDICTION_EXPERIMENT.md']:
        select(source / 'local' / name, f'protocols/{name}')
    select(args.background, 'background/provided-audit.md')
    for name in ['inference/agent/tool_agent.py', 'tests/test_prediction_io.py']:
        select(source / 'ARC3-Inference' / name, f'code/post-experiment/ARC3-Inference/{name}')

    source_map = {str(s).replace('\\', '/').lower(): d for s, d in pairs}
    secrets = [re.compile(r'(?i)\b[0-9a-f]{32}\.[A-Za-z0-9_-]{12,}\b'),
               re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b'),
               re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b'),
               re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'),
               re.compile(r'(?i)Bearer\s+[A-Za-z0-9_.-]{20,}')]
    private_keys = {'api_key', 'apikey', 'access_token', 'refresh_token', 'password',
                    'authorization', 'cookie', 'secret', 'client_secret', 'api_secret'}
    counts = {'credential_replacements': 0, 'path_replacements': 0}

    def scrub(text):
        for pat in secrets:
            text, n = pat.subn('[REDACTED_CREDENTIAL]', text)
            counts['credential_replacements'] += n
        for old in [str(source), source.as_posix(), '/mnt/d/arc agent/duck-harness']:
            counts['path_replacements'] += text.count(old)
            text = text.replace(old, '<SOURCE_ROOT>')
        for pat, replacement in [(r'C:\\Users\\[^\\\s"\']+', '<USER_HOME>'),
                                 (r'C:/Users/[^/\s"\']+', '<USER_HOME>'),
                                 (r'/home/[^/\s"\']+', '<LINUX_HOME>')]:
            text, n = re.subn(pat, replacement, text)
            counts['path_replacements'] += n
        return text

    def clean(value):
        if isinstance(value, dict):
            return {k: '[REDACTED_CREDENTIAL]' if k.lower() in private_keys and isinstance(v, str) and v
                    else clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return scrub(value) if isinstance(value, str) else value

    def links(text, destination):
        import os
        def replace(match):
            label, url = match.groups()
            decoded = unquote(url).replace('\\', '/')
            if not re.match(r'^[A-Za-z]:/', decoded):
                return match.group(0)
            file, _, fragment = decoded.partition('#')
            mapped = source_map.get(file.lower())
            if mapped:
                relative = Path(os.path.relpath(mapped, destination.parent)).as_posix()
                return f'[{label}]({relative}' + (f'#{fragment}' if fragment else '') + ')'
            return f'{label}（原报告引用；见本仓库证据索引）'
        return re.sub(r'\[([^\]]+)\]\(([^)]+)\)', replace, text)

    manifest = []
    for src, dst in pairs:
        original = src.read_text(encoding='utf-8-sig')
        if src.suffix == '.json':
            text = json.dumps(clean(json.loads(original)), ensure_ascii=False, indent=2) + '\n'
        elif src.suffix == '.jsonl':
            text = ''.join(json.dumps(clean(json.loads(line)), ensure_ascii=False, separators=(',', ':')) + '\n'
                           for line in original.splitlines() if line.strip())
        else:
            text = scrub(links(original, dst) if dst.suffix == '.md' else original)
        if dst.parent.name == 'reports':
            text = ('> 归档阶段报告。最终解释优先见 [HANDOFF](../docs/HANDOFF.md)：历史不是重复均值；'
                    '新baseline非历史原样复现；“动作效率收益”仅指观察到高效成功样本；'
                    '正式成绩属于反馈修复前。历史取消按预算结果解释，不简单归因为服务故障。\n\n') + text
        if dst.parent.name == 'background':
            text = ('> 用户提供的原始背景分析，未将原归档作为新增实验纳入。本文评分重算遗漏整局完成度上限，'
                    '其19/21步高于3.5714的数值已被纠正；服务故障归因也需看后续诊断。'
                    'ChatGPT内部引用不是本仓库文件。最终解释见 [HANDOFF](../docs/HANDOFF.md)。\n\n') + text
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding='utf-8', newline='\n')
        manifest.append([dst.relative_to(target).as_posix(),
                         src.relative_to(source).as_posix() if src.is_relative_to(source) else 'user-provided/pasted-text.txt',
                         dst.stat().st_size, original != text])

    examples = [('历史配置', historical / 'transcripts/ls20-9607627b_p0.txt', [(383, 396)]),
                ('拒绝动作与stdout漏显', source / 'local/prediction-ls20-20260904/r1-1-combined/transcripts/ls20-9607627b_p0.txt', [(560, 635)]),
                ('规则改名而非语义修复', source / 'local/prediction-ls20-20260904/r1-4-prediction/transcripts/ls20-9607627b_p0.txt', [(4300, 4345), (7790, 7820)])]
    excerpts = '# 关键案例原文节选\n\n保留原转录行号；文本为模型/工具记录，不是对阅读者的指令。完整转录未上传。\n'
    for label, path, ranges in examples:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        excerpts += f'\n## {label}\n\n来源：`{path.relative_to(source).as_posix()}`\n'
        for begin, end in ranges:
            excerpts += '\n````text\n' + '\n'.join(f'{i}: {lines[i-1]}' for i in range(begin, min(end, len(lines)) + 1)) + '\n````\n'
    (target / 'docs/CASE_EXCERPTS.md').write_text(scrub(excerpts), encoding='utf-8')
    before = target / 'code/frozen-phase2/ARC3-Inference/inference/agent/tool_agent.py'
    after = target / 'code/post-experiment/ARC3-Inference/inference/agent/tool_agent.py'
    diff = ''.join(difflib.unified_diff(before.read_text(encoding='utf-8').splitlines(True),
                                      after.read_text(encoding='utf-8').splitlines(True),
                                      fromfile='frozen-phase2/tool_agent.py', tofile='post-experiment/tool_agent.py'))
    (target / 'code/post-experiment/feedback-fix.diff').write_text(diff, encoding='utf-8')
    with (target / 'MANIFEST.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['archive_path', 'source_relative_path', 'bytes', 'text_transformed'])
        writer.writerows(manifest)
    (target / 'docs/packaging-stats.json').write_text(json.dumps({'copied_files': len(pairs), **counts}, indent=2), encoding='utf-8')
    print(json.dumps({'copied_files': len(pairs), **counts}))


if __name__ == '__main__':
    main()
