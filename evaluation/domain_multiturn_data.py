"""Add role-alternating, explicitly labelled conversation relations to domain data.

Relations are dataset metadata, never an additional online classifier output.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from application.domain_encoder import DOMAINS, DEFER_DOMAIN
from application.encoder_input import EncoderInput


def active_domain(relation, previous, current=None):
    if relation == "continuation":
        return previous
    if relation == "replacement":
        return current
    if relation == "addition":
        # Both requests are explicitly retained, including a capability question.
        return previous if previous == current else DEFER_DOMAIN
    if relation in {"withdrawal", "unresolved_reference"}:
        return DEFER_DOMAIN
    raise ValueError("unknown conversation relation")


FOLLOWUPS = {
    "zh": {
        "general": ("想知道服务内容还是使用方式？", "两者都介绍。"),
        "product_technical": ("想先了解操作方法还是技术参数？", "这两方面都想了解。"),
        "order_logistics": ("你知道订单现在的状态吗？", "不清楚，所以想查询和了解处理方式。"),
        "billing_refund": ("只是了解信息，还是马上办理？", "先只了解，不要提交。"),
        "account_security": ("目前能正常进入账号吗？", "不能正常进入。"),
        "human_service": ("你想继续自动服务还是联系工作人员？", "请联系工作人员。"),
    },
    "en": {
        "general": ("Would you like to know the services or how to use them?", "Please explain both."),
        "product_technical": ("Are you interested in usage instructions or specifications?", "I'd like to understand both."),
        "order_logistics": ("Do you know the current order status?", "No, I need to check and understand my options."),
        "billing_refund": ("Do you need information or want to proceed now?", "Information only. Don't submit anything."),
        "account_security": ("Can you access your account normally?", "No, I cannot access it normally."),
        "human_service": ("Would you prefer automated help or a staff member?", "Please contact a staff member."),
    },
}


def history(language, owner, task, question, depth, old_task):
    messages = [("user", task), ("assistant", question)]
    if depth == 4:
        followup, answer = FOLLOWUPS[language][owner]
        messages = [("user", task), ("assistant", followup), ("user", answer), ("assistant", question)]
    elif depth == 6:
        # Explicitly withdrawn business history must not outweigh the new request.
        prefix = [("user", old_task), ("assistant", question),
                  ("user", "刚才说错了，那件事不用处理。" if language == "zh" else "I was mistaken. Drop that request."),
                  ("assistant", "好的，还有其他问题吗？" if language == "zh" else "Understood. Is there another question?")]
        messages = prefix + messages
    return [{"role": role, "content": content} for role, content in messages]


def additions(seed, language, split):
    scenarios = seed["scenarios"][language][split]
    forms = seed["expressions"][language][split]
    for owner, tasks in scenarios.items():
        for index, task in enumerate(tasks):
            family = f"domain-v2:{language}:{split}:{owner}:{index}"
            outputs = []
            for relation, key in (("continuation", "continue"), ("withdrawal", "withdraw")):
                outputs.extend((relation, text, None) for text in forms[key])
            for target, targets in scenarios.items():
                # General here means courtesy/capability help, not an unbounded catch-all.
                for next_task in targets:
                    if next_task == task:
                        continue
                    outputs.extend((relation, text.replace("{next}", next_task), target)
                                   for relation, key in (("replacement", "replace"), ("addition", "add"))
                                   for text in forms[key])
            for n, (relation, text, target) in enumerate(outputs):
                depth = (2, 4, 6)[n % 3]
                old_owner = DOMAINS[(DOMAINS.index(owner) + 1 + n % (len(DOMAINS)-1)) % len(DOMAINS)]
                old_task = scenarios[old_owner][index % len(scenarios[old_owner])]
                yield {"case_id": f"{family}:{n}", "group_id": family, "language": language,
                    "label": active_domain(relation, owner, target), "relation": relation,
                    "previous_domain": owner, "current_domain": target,
                    "source": "authored-domain-dialogue-v2", "text": text,
                    "messages": history(language, owner, task, forms["question"][n % len(forms["question"])], depth, old_task)}
    # No business referent exists in this history; no guessed domain is justified.
    for i, text in enumerate(forms["unknown"]):
        yield {"case_id": f"domain-v2:{language}:{split}:unknown:{i}",
            "group_id": f"domain-v2:{language}:{split}:unknown", "language": language,
            "label": DEFER_DOMAIN, "relation": "unresolved_reference", "text": text,
            "source": "authored-domain-dialogue-v2",
            "messages": [{"role": "user", "content": "你好" if language == "zh" else "Hi"},
                         {"role": "assistant", "content": "有什么需要帮助？" if language == "zh" else "How can I help?"}]}


def build(output):
    if output.exists():
        raise ValueError("dataset output exists")
    source = Path("data/training/domain-multiturn-seeds-v2.json")
    seed = json.loads(source.read_text())
    manifest = {"source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "scope": "Synthetic role-alternating augmentation plus immutable v1 development data; not independent gold", "splits": {}}
    seen, families = set(), {}
    for language in ("zh", "en"):
        for split in ("train", "calibration", "heldout"):
            base = Path(f"data/training/domain-encoder-v1/{language}/{split}.jsonl")
            rows = [json.loads(line) for line in base.read_text().splitlines()]
            rows += list(additions(seed, language, split))
            result = []
            for row in rows:
                identity = (language, EncoderInput.from_record(row).identity())
                if identity in seen:
                    raise ValueError("duplicate input across domain dataset")
                seen.add(identity)
                if families.setdefault(row["group_id"], split) != split:
                    raise ValueError("conversation family crosses split")
                if row["label"] not in {*DOMAINS, DEFER_DOMAIN}:
                    raise ValueError("unknown domain")
                result.append(row)
            destination = output / language / f"{split}.jsonl"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in result))
            manifest["splits"][f"{language}/{split}"] = {"count": len(result), "base_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "history_lengths": dict(Counter(len(r.get("messages", ())) for r in result)),
                "relations": dict(Counter(r.get("relation", "v1") for r in result))}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest["splits"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(**vars(parser.parse_args()))
