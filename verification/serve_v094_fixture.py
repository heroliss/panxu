"""Disposable provenance peer for file analysis and re-analysis over local SSE.

Every task goal uses the existing bounded advice/plan fixture branch so UI
analysis entry points can be exercised without cloud credentials or file writes.
Connection probes retain the v0.9 fixed-data behavior.
"""
import serve_v050_fixture as fixture

plan_answer = fixture.answer

import serve_v090_fixture as stream_fixture

base_answer = stream_fixture.answer


def answer(payload):
    if stream_fixture.context(payload).get('goal'):
        return plan_answer(payload)
    return base_answer(payload)


if __name__ == '__main__':
    stream_fixture.answer = answer
    stream_fixture.main()
