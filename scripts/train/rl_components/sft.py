"""SFT message conversion without importing datasets, Torch, or TRL."""
import hashlib
import json
from pathlib import Path


def sft_input_receipt(config_path, model_path, train, val):
    """Record worker inputs without loading or rehashing model weights."""
    model_path = Path(model_path)
    metadata = ('config.json', 'generation_config.json', 'tokenizer_config.json', 'tokenizer.json',
                'special_tokens_map.json', 'added_tokens.json', 'vocab.json', 'merges.txt', 'chat_template.jinja')
    return dict(config_path=str(config_path), config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
                train=train.source_receipt, val=val.source_receipt, model_path=str(model_path),
                model_metadata_sha256={name: hashlib.sha256((model_path / name).read_bytes()).hexdigest()
                                       for name in metadata if (model_path / name).is_file()},
                weight_files=[dict(name=path.name, bytes=path.stat().st_size)
                              for path in sorted(model_path.glob('*'))
                              if path.suffix in ('.safetensors', '.bin')],
                weight_content_sha256_recorded=False)


def chat_token_lengths(messages, tokenizer):
    """Count the exact generation suffix, including the chat terminator."""
    full = tokenizer.apply_chat_template(messages, tokenize=True)
    prefix = tokenizer.apply_chat_template(messages[:1], tokenize=True, add_generation_prompt=True)
    if full[:len(prefix)] != prefix:
        raise ValueError('SFT chat does not preserve the generation prompt')
    return len(full), len(full) - len(prefix)


class SFTDataset:
    """Converted prompt/completion records for Dataset.from_list.

    Keep the first message as the prompt and all remaining messages as the
    completion. Preserve the existing truthy limit rule, including limit=0.
    """

    def __init__(self, records):
        self.records = records

    def validate_token_budget(self, tokenizer, max_length, max_action_tokens=None):
        """Refuse oversized targets before loading model weights; never truncate."""
        lengths = []
        for index, record in enumerate(self.records, 1):
            total, completion = chat_token_lengths(record['prompt'] + record['completion'], tokenizer)
            if total >= max_length:
                raise ValueError(f'SFT row {index} reaches max_length {max_length}')
            if max_action_tokens is not None and completion > max_action_tokens:
                raise ValueError(f'SFT row {index} exceeds action token limit {max_action_tokens}: {completion}')
            lengths.append(total)
        return lengths

    @classmethod
    def from_jsonl(cls, path, limit=None):
        records = []
        digest = hashlib.sha256()
        with open(path, 'rb') as stream:
            for line in stream:
                digest.update(line)
                if limit and records and len(records) >= limit:
                    continue
                row = json.loads(line)
                records.append(dict(prompt=row['messages'][:1], completion=row['messages'][1:], format=row['format']))
        dataset = cls(records)
        dataset.source_receipt = dict(path=str(path), file_sha256=digest.hexdigest(), selected_rows=len(records),
                                     requested_limit=limit, selected_records_sha256=hashlib.sha256(
                                         json.dumps(records, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        return dataset
