import os
import sys
import asyncpraw
from summoner.client import SummonerClient
from aioconsole import ainput
import asyncio

# Reddit API credentials (replace with your own credentials)
reddit = asyncpraw.Reddit(
    client_id='14uiUOEPKLBhdq7jGlW-Fw',
    client_secret='-YN1H2PqQk_siLXFNiDAh0vZYkoLbw',
    username='SummonerNetwork',
    password='ElliottRemyKevin123',
    user_agent='bot by u/SummonerNetwork'
)

async def process_submission(submission):
    print(f"New submission detected: {submission.title}")
    # Your bot's logic goes here
    # Example: Reply to the submission (be cautious not to spam!)
    # submission.reply("Hello, I'm a bot listening to new submissions!")

threads_buffer = []

async def run_myagent():
    myagent = SummonerClient(name="MyAgent", option="python")
        
    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        pass

    @myagent.send(route="custom_send")
    async def custom_send():
        while len(threads_buffer) > 0:
            submission = threads_buffer.pop(0)
            return submission

    # Use asyncio.create_task to avoid blocking the event loop
    await myagent.run(host="127.0.0.1", port=8888)

async def main():
    # Run the agent in a separate task
    asyncio.create_task(run_myagent())

    # Main loop to listen to subreddit submissions
    while True:
        try:
            # Subreddit to listen to
            subreddit = await reddit.subreddit('AI_agents')
            async for submission in subreddit.new():
                threads_buffer.append(submission)
        except Exception as e:
            print(f"An error occurred: {e}")
            await asyncio.sleep(60)  # Wait a bit before continuing in case of error

if __name__ == "__main__":
    # Use the current event loop instead of asyncio.run()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())