"""Thin artifact writer for real-chain command-primary evaluation."""
from __future__ import annotations

from evaluation.command_primary_eval.direct_runner import DirectRunner
from evaluation.command_primary_eval.e2e import ChatApplicationE2EAdapter


class ChatApplicationE2ERunner(DirectRunner):
    def __init__(self, adapter: ChatApplicationE2EAdapter) -> None:
        super().__init__(
            adapter,
            component="chat_application_e2e",
            evaluator_version="chat-application-e2e-runner-v1",
        )
