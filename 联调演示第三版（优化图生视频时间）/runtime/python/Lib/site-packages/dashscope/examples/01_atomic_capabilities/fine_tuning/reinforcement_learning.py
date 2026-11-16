# -*- coding: utf-8 -*-
# Copyright (c) Alibaba, Inc. and its affiliates.
"""dashscope 2.0 agentic RL example: list jobs, and (optionally) the
full register-functions -> upload-data -> create -> wait lifecycle.

The read-only list/get sections run as-is. The training workflow is
commented out: it registers real rollout/reward function instances,
uploads datasets and submits a real training job (which trains and
bills). Uncomment it once your function classes, data files and
training config are ready.
"""

import dashscope

# Authentication: the client reads the API key from the
# DASHSCOPE_API_KEY environment variable by default; you can also
# pass it explicitly via dashscope.Client(api_key="sk-xxx").

if __name__ == "__main__":
    client = dashscope.Client()

    # List RL jobs (paged; RL jobs share the fine-tune wire contract)
    print("----- list RL jobs -----")
    page = client.rl.jobs.list(page_no=1, page_size=10)
    print(f"total: {page.total_count}")
    for job in page.jobs:
        print(
            f"job_id={job.job_id}, status={job.status}, "
            f"model={job.finetuned_model}",
        )

    # Get one job by ID (uses the first job of the list above)
    if page.jobs:
        print("----- get RL job -----")
        job = client.rl.jobs.fetch(page.jobs[0].job_id)
        print(
            f"job_id={job.job_id}, status={job.status}, "
            f"finished={job.finished}",
        )

    # Full lifecycle: register functions -> upload data -> create ->
    # wait -> delete. Uncomment once you have rollout/reward function
    # classes, training data files and a training config file.
    #
    # print("----- register functions -----")
    # registered = client.rl.register_functions(
    #     rollout_classpaths=["my_rl.rollout.MyRollout"],
    #     reward_classpaths=["my_rl.reward.MyReward"],
    #     workspace_dir="./",
    # )
    # print(f"registered: {registered}")
    #
    # print("----- upload data -----")
    # data = client.rl.upload_data(
    #     training_files=["train.jsonl"],
    #     validation_files=["val.jsonl"],
    # )
    # print(f"training_file_ids={data.training_file_ids}")
    #
    # print("----- create RL job -----")
    # job = client.rl.jobs.create(
    #     config_path="rl_config.yaml",
    #     job_name="my-rl-job",
    # )
    # print(f"created job_id={job.job_id}")
    #
    # print("----- wait RL job -----")
    # final = client.rl.jobs.wait(job)
    # print(f"status={final.status}, succeeded={final.succeeded}")
    #
    # print("----- delete RL job -----")
    # client.rl.jobs.delete(job.job_id)


# --- Production springboard (Best Practices & Patterns) -------
# Minimal runnable atomic call above; to productionize it, see:
#   Offline mock debugging: 02_advanced_engineering/local_mock_debugging.py
#   Backoff + breaker DR: 02_advanced_engineering/circuit_breaker_retry.py
#   This capability is a long-running jobs flow; reuse the
#   generic patterns above for poll timeouts and retries.
#   client.rl wraps the async workflow with asyncio.run -- under a
#   running event loop (Jupyter, FastAPI) use dashscope.AsyncClient.rl.
# -------------------------------------------------------------
