import json
import ast

transcript_path = r'C:\Users\cmlak\.gemini\antigravity-ide\brain\a0a02e52-1978-45d1-8c2f-93d855c88af8\.system_generated\logs\transcript_full.jsonl'
file_path = r'c:\bakertilly\agentic_auto\agentic_platform\account\management\commands\run_doc_agent.py'

content = open(file_path, 'r', encoding='utf-8').read()

for line in open(transcript_path, 'r', encoding='utf-8'):
    if 'tool_calls' in line and 'run_doc_agent.py' in line and 'multi_replace_file_content' in line:
        data = json.loads(line)
        calls = data.get('tool_calls', [])
        for call in calls:
            if call.get('name') == 'multi_replace_file_content':
                args = call.get('args', {})
                if 'run_doc_agent.py' in args.get('TargetFile', ''):
                    chunks = args.get('ReplacementChunks', '[]')
                    if isinstance(chunks, str):
                        try:
                            chunks = json.loads(chunks)
                        except:
                            pass
                    if not isinstance(chunks, list):
                        continue
                    
                    for chunk in chunks:
                        target = chunk.get('TargetContent')
                        replacement = chunk.get('ReplacementContent')
                        if target and replacement:
                            if target in content:
                                print(f"Applying chunk for {args.get('Description')}")
                                content = content.replace(target, replacement)
                            else:
                                print(f"Target not found for {args.get('Description')}")

open(file_path, 'w', encoding='utf-8').write(content)
print("Done.")
