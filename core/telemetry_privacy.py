"""Export-only scope policy; traversal and text replacement belong to libraries."""
from __future__ import annotations

import json
import os
import re
import jwt
from phonenumbers import PhoneNumberMatcher

from boltons.iterutils import default_enter, default_exit, remap
from pydantic_core import to_jsonable_python
from jsonschema import Draft202012Validator, SchemaError
from scrubadub import Scrubber
from scrubadub.detectors import EmailDetector, RegexDetector, UserSuppliedFilthDetector
from scrubadub.post_processors import FilthReplacer, PrefixSuffixReplacer

REDACTED = "[REDACTED]"
SENSITIVE_FIELDS = frozenset({
    "authorization", "password", "passwd", "secret", "apikey", "token",
    "accesstoken", "refreshtoken", "cookie", "setcookie", "otp", "verificationcode",
    "email", "phone", "phonenumber", "address", "address1", "address2",
    "firstname", "lastname", "fullname", "cardnumber", "cvv", "cvc",
    "thinking", "reasoning", "reasoningcontent", "signature",
})
SCHEMA_MAPS = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
SCHEMA_ARRAYS = {"allOf", "anyOf", "oneOf", "prefixItems"}
SCHEMA_VALUES = {"items", "contains", "additionalProperties", "unevaluatedProperties",
                 "unevaluatedItems", "propertyNames", "not", "if", "then", "else", "contentSchema"}
# Declarative scope for scrubadub's detector. Matching spans and replacement
# stay in the library, including quoted/escaped values; no shell-text parser.
class CredentialTextDetector(RegexDetector):
    name = "scoped_credentials"
    regex = re.compile(
        r'''\b(?:api[_-]?key|password|passwd|secret|token)\s*[:=]\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;]+)|\bBearer\s+[A-Za-z0-9._~+/=-]+''',
        re.IGNORECASE,
    )


def sensitive_field(key):
    return str(key).casefold().replace("_", "").replace("-", "") in SENSITIVE_FIELDS


def _text(value, secrets):
    # No numeric/entropy heuristics: a business identifier is not a credential.
    known = set(secrets)
    known.update(match.raw_string for match in PhoneNumberMatcher(value, "US")
                 if match.raw_string.startswith("+"))
    for word in value.split():
        if word.count(".") == 2:
            try:
                jwt.decode(word, options={"verify_signature": False})
            except jwt.PyJWTError:
                pass
            else:
                known.add(word)
    detectors = [EmailDetector(), CredentialTextDetector()]
    if known:
        detectors.append(UserSuppliedFilthDetector([
            {"match": secret, "filth_type": "unknown"} for secret in known if secret
        ]))
    return Scrubber(detector_list=detectors, post_processor_list=[
        FilthReplacer(), PrefixSuffixReplacer(prefix="[REDACTED:", suffix="]"),
    ]).clean(value)


def mask_telemetry(data, *, secrets=()):
    """Return a detached JSON-compatible observation; never rewrite dictionary keys."""
    known = tuple(secrets) + tuple(value for key, value in os.environ.items()
        if value and key.endswith(("_API_KEY", "_SECRET_KEY", "_PASSWORD", "_ACCESS_TOKEN")))
    normalized = to_jsonable_python(data, fallback=str)
    # remap memoizes containers by identity; keep decoded containers alive until
    # traversal finishes so different JSON strings cannot reuse an identity.
    decoded_containers = []
    schema_paths = set()

    def sensitive_value(path, key):
        # JSON Schema property names declare inputs; they are not user values.
        declaration = path and path[-1] in SCHEMA_MAPS and path[:-1] in schema_paths
        return sensitive_field(key) and not declaration

    def register_schema(path, key, value):
        # Propagate schema ownership only along JSON Schema's schema-valued
        # keywords, never through instance-valued defaults/examples/extensions.
        child = (path in schema_paths and key in SCHEMA_VALUES) or (
            path and path[:-1] in schema_paths and (
                path[-1] in SCHEMA_MAPS or (path[-1] in SCHEMA_ARRAYS and isinstance(key, int))))
        if child and isinstance(value, dict):
            schema_paths.add(path + (key,))
            return
        if any(path[:len(root)] == root for root in schema_paths):
            return
        types = value.get("type") if isinstance(value, dict) else None
        object_allowed = types == "object" or (isinstance(types, list) and "object" in types)
        if object_allowed and "properties" in value:
            try:
                Draft202012Validator.check_schema(value)
            except SchemaError:
                pass  # ordinary instance data is not a schema exemption
            else:
                schema_paths.add(path + (key,))

    def enter(path, key, value):
        register_schema(path, key, value)
        if key == "thinking" and value == {"type": "disabled"}:
            return value.copy(), False
        if sensitive_value(path, key):
            return REDACTED, False
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, (dict, list)):
                decoded_containers.append(decoded)
                register_schema(path, key, decoded)
                return default_enter(path, key, decoded)
        if isinstance(value, dict) and value.get("type") in ("thinking", "reasoning", "redacted_thinking"):
            return {"type": value["type"], "content": REDACTED}, False
        return default_enter(path, key, value)

    def exit(path, key, old, new, items):
        if isinstance(old, str):
            result = default_exit(path, key, new, new, items)
            return json.dumps(result, ensure_ascii=False)
        return default_exit(path, key, old, new, items)

    def visit(path, key, value):
        if key == "thinking" and value == {"type": "disabled"}:
            return key, value.copy()
        if sensitive_value(path, key):
            return key, REDACTED
        if isinstance(value, dict) and value.get("type") in ("thinking", "reasoning", "redacted_thinking"):
            return key, {"type": value["type"], "content": REDACTED}
        # Encoded JSON has already been transformed structurally by enter/exit.
        if isinstance(value, str) and value != REDACTED:
            try:
                decoded = json.loads(value)
            except ValueError:
                decoded = None
            if isinstance(decoded, dict) and decoded.get("type") in ("thinking", "reasoning", "redacted_thinking"):
                value = json.dumps({"type": decoded["type"], "content": REDACTED})
            elif not isinstance(decoded, (dict, list)):
                value = _text(value, known)
        return key, value

    return remap({"observation": normalized}, enter=enter, exit=exit, visit=visit)["observation"]
