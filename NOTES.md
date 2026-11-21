# Notes

## Time spent
It took me 2.5 to 3 hours to understand and build on the problem. I used Claude Code to help me with the tasks.
## What I did
I first read thoroughly to understand the problem.
Next I divided the tasks:
- Get the task list given the md file using an LLM (feat/extract_tasks) 
- Create schedule given the task list using deterministic Python (feat/schedule_tasks)
- Deployed a public app and added logging (feat/deploy_URL)
- "Generated sample review outputs into `data/tasks/` `data/schedule` using scripts. (feat/add_data)

All the branches are present in the git repo.
## What I cut and why
I had to make a choice of implementing the core tasks in the backend first given the time. Therefore these tasks were left undone:
- UI is the same basic one, no changes have been made.
- In case there are errors in the review process, nothing is displayed on the UI and logs need to be inspected.
- Extra information as to when the tasks can be done if before the deadline are not explicitly shown in the UI - need to be inspected from the output of `/api/plans/{plan_id}`
## What is not ready
- The UI is not ready.
- The model currently being used is Claude Haiku-5-5 to adhere to the time limit. However, the answers are not consistent and using a more complex model and/or higher effort will help.
- Having a database in the app will greatly help since every time the same plan is queried, the model is asked to respond on the fly.
## What I would do next and a rough estimate
- Improve the UI based on `BRAND.md`. Further add a login to the app for different users. Also allow for users to enter their own API key, so that the app provider doesnt have to pay for all requests. -2 hrs
- Add a database - 2 hrs
- Test more on which model to use. For testing I will use ground truth in `data/reviews/`, use different models to create the reviews and test which one is consistent on x-number of calls given the same plan, along with time taken. In the end we want a good model which answers quickly. -3 hrs
- I would want to test using a complex model to create a summary of important things in the plan and an efficient model to then create the review. Essentially ensuring correctness and consistency of the output. (needs exploration and discussion) 
## How I used AI tools
I used Claude Code to code and prompted it with my requirements. I manually went through the code to check if it was doing what I expected. For `backend/app/schedule_tasks.py`, I manually did a dry run on `data/reviews/test.json`. One example of an error I found, when I ran `backend/app/extract_tasks.py` I ran into Auth error when calling the model, since .env was not being loaded. Claude Code made an error when extracting .env variables which I solved with adding `dotenv`.

Additionally, all the outputs pass the contract check using `check_contract.py`. 

## Environment variables my review command needs
```
ANTHROPIC_API_KEY= #Claude API Key
EXTRACT_MODEL=claude-haiku-5-5
```
## Known Caveats
Please know that an error in scheduling can come from a wrong review created by the LLM call - meaning we need to test more on model selection and prompts when it comes to creating review from the plan.

For example: for harbour-run plan sometimes I get a schedule and the other times I get a cycle. This is due the review inaccurately being created by the LLM call. Also, in the harbour-run plan the deadline is mentioned twice (5 weeks and 99 days) which makes it confusing for the model to decide.