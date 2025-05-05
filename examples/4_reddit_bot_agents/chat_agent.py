import os
import sys
import asyncpraw
from summoner.client import SummonerClient
from aioconsole import ainput
import asyncio

# Reddit API credentials (replace with your own credentials)

if __name__ == "__main__":
    myagent = SummonerClient(name="MyAgent", option = "python")
    reddit = asyncpraw.Reddit(
        client_id='14uiUOEPKLBhdq7jGlW-Fw',
        client_secret='-YN1H2PqQk_siLXFNiDAh0vZYkoLbw',
        username='SummonerNetwork',
        password='ElliottRemyKevin123',
        user_agent='bot by u/SummonerNetwork'
    )

    @myagent.receive(route="custom_receive")
    async def custom_receive(msg):
        pass

    @myagent.send(route="custom_send")
    async def custom_send():
        try:
            # Subreddit to listen to
            subreddit = await reddit.subreddit('AI_agents')
            async for submission in subreddit.new():
                serializable = {
                    "title": submission.title,
                    "author": str(submission.author),
                    "url": submission.url,
                    "selftext": submission.selftext,
                    "created_utc": submission.created_utc
                }
                print(f"Got new poast: {serializable}")
                return serializable
        except Exception as e:
            print(f"An error occurred: {e}")
            await asyncio.sleep(60)  # Wait a bit before continuing in case of error

myagent.run(host = "127.0.0.1", port = 8888)