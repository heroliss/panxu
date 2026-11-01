"""Disposable UI peer for explicit delete-plan labels, using local SSE only.

Goals beginning with ``验证删除操作`` use the existing v0.5 plan branch.
Other goals, connection probes and the SSE transport retain v0.9 behavior.
"""
import serve_v050_fixture as fixture

plan_answer = fixture.answer

import serve_v090_fixture as stream_fixture

base_answer = stream_fixture.answer


def answer(payload):
    if str(stream_fixture.context(payload).get('goal', '')).startswith('验证删除操作'):
        return plan_answer(payload)
    return base_answer(payload)


if __name__ == '__main__':
    stream_fixture.answer = answer
    stream_fixture.main()
