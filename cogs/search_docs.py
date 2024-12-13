from discord.ext import commands
from discord import app_commands, Embed
import discord
import requests
import os
from openai import OpenAI
from datetime import datetime
class SearchDocs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client=  OpenAI(
            base_url=os.getenv("OPENAI_API_BASE"),
            api_key=os.getenv("OPENAI_API_KEY"),
        )

    @app_commands.command(
        name="search",
        description="Search for information in Ente's documentation"
    )
    @app_commands.describe(query="The query to search for")
    async def search(self, interaction: discord.Interaction, query: str):
        # ask searchie to search for the query
        response = requests.get(
            f"{os.getenv('SEARCHIE_API_URL')}/api/datasets/{os.getenv('SEARCHIE_DATASET_ID')}/search?q={query}&limit=5"
        )
        if response.status_code != 200:
            await interaction.response.send_message("Failed to search for the query")
            return
        res = response.json()
        chunk_results = res["chunk_results"]
        datapoints = res["datapoints"]

        # defer the response
        await interaction.response.defer()

        formatted_chunk_results = ""

        embed = Embed(
            title=f"Ente Knowledge Base Search",
            color=0x5fff80,
            description=f"Here are the results for your query: `{query}`.",
            timestamp=datetime.now()
        )

        # give it the results in XML format since that's LLM friendly
        for chunk in chunk_results:
            for datapoint in datapoints:
                if datapoint["id"] == chunk["datapoint_id"]:
                    formatted_chunk_results += f"<document title=\"{datapoint['name']}\">\n{datapoint['data']}\n</document>\n"
                    # add fields to the embed
                    # data only 256 characters
                    if len(chunk["data"]) > 256:
                        chunk["data"] = chunk["data"][:256] + "..."
                    embed.add_field(name=datapoint["name"], value=chunk["data"], inline=False)

        try:
            print("Creating completion...")
            completion = self.client.chat.completions.create(
                model=os.getenv('OPENAI_MODEL'),
                messages=[
                    {
                        "role": "system",
                        "content": "You are \"Ducky\", a pleasantly helpful and intelligent assistant that answers questions about Ente using provided documentation. Ente is a privacy-focused end-to-end encrypted photo storage service. When providing answers, you should be concise and to the point. Do not ask the user any questions. If you are unable to extract information from the provided documentation, simply say that you could not find the answer."
                    },
                    {
                        "role": "user",
                        "content": f"My query is: \"{query}\". Here are the related documents that I found:\n\n{formatted_chunk_results}"
                    },
                ]
            )
            
            print(f"API Response: {completion}")
            
            if completion and hasattr(completion, 'choices') and completion.choices:
                synthesized_answer = completion.choices[0].message.content
            else:
                synthesized_answer = "Sorry, I couldn't generate a response at this time."

            return await interaction.followup.send(embed=embed, content=synthesized_answer)
        except Exception as e:
            print(f"OpenAI API error: {str(e)}")
            return await interaction.followup.send("Failed to process the query")

async def setup(bot):
    await bot.add_cog(SearchDocs(bot))
