# -*- coding: utf-8 -*-
# Copyright (c) Alibaba, Inc. and its affiliates.
"""dashscope 2.0 agent recipe: create and run a Managed Agent deployment.

Requires DASHSCOPE_API_KEY; DASHSCOPE_WORKSPACE selects the
AgentStudio workspace (defaults to trial).
"""

import os

import dashscope
from dashscope.agentstudio import user_message


def main() -> None:
    workspace = os.environ.get("DASHSCOPE_WORKSPACE", "trial")
    with dashscope.Client(workspace=workspace) as client:
        deployment = client.agentstudio.deployments.create(
            name="daily-summary",
            description="Generate a daily summary",
            agent={"id": "agent_xxx"},
            schedule={
                "type": "cron",
                "expression": "0 9 * * 1-5",
                "timezone": "Asia/Shanghai",
            },
            initial_events=[user_message("Summarize yesterday's orders")],
            metadata={"biz": "summary"},
        )
        print("deployment:", deployment.id, deployment.status)

        run = client.agentstudio.deployments.run(deployment.id)
        print("run:", run.id, run.status)

        for item in client.agentstudio.deployments.list_runs(
            deployment.id,
            limit=20,
        ):
            print(item.id, item.trigger_source, item.status)


if __name__ == "__main__":
    main()
