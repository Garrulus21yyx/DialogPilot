"""Shared answer-level coverage semantics, independent of task execution status."""
ANSWER_COVERAGE = (
    "Evaluate coverage against the user's requested information, not just the topic of the evidence. "
    "Task/requirement coverage and SUCCEEDED or complete flags describe execution, not whether the reply answers the request. "
    "Preserve the requested kind of information: conditions do not by themselves explain a procedure; "
    "a status does not by itself explain a cause or a time estimate. "
    "For each requested aspect, provide the supported information or explicitly identify the missing detail. "
    "When sources contain only part of what is requested, say precisely what they establish and what they do not specify. "
    "Do not fill gaps with invented steps, assume the service is unavailable, promise later resolution, or silently omit the gap. "
    "A precise evidence limitation can address an information request without completing a business action. "
    "Do not demand additional details that the user did not request, or treat a concise sufficient answer as incomplete. "
)
