import discord
from discord.ext import commands
import tempfile
import zipfile
import os


class FlattenZip(commands.Cog):
    """
    /flattenzip: Accepts a zip file, flattens it, and returns a new zip with all files at root.
    """

    def __init__(self, bot):
        self.bot = bot

    @discord.app_commands.command(
        name="flattenzip",
        description="Flatten all directories in a zip file (all files to root, no folders).",
    )
    @discord.app_commands.describe(zip_file="Upload your .zip file here")
    async def flattenzip(
        self, interaction: discord.Interaction, zip_file: discord.Attachment
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        if not zip_file.filename.lower().endswith(".zip"):
            await interaction.followup.send(
                "Please upload a valid zip file.", ephemeral=True
            )
            return

        with tempfile.TemporaryDirectory() as tempdir:
            in_path = os.path.join(tempdir, zip_file.filename)
            await zip_file.save(in_path)

            out_dir = os.path.join(tempdir, "flattened")
            os.makedirs(out_dir, exist_ok=True)

            with zipfile.ZipFile(in_path, "r") as zip_in:
                for member in zip_in.infolist():
                    if member.is_dir():
                        continue
                    filename = os.path.basename(member.filename)
                    if not filename:
                        continue
                    dest = os.path.join(out_dir, filename)
                    base, ext = os.path.splitext(filename)
                    count = 1
                    while os.path.exists(dest):
                        dest = os.path.join(out_dir, f"{base}_{count}{ext}")
                        count += 1
                    with open(dest, "wb") as f:
                        f.write(zip_in.read(member))

            out_zip = os.path.join(tempdir, "flattened.zip")
            with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zip_out:
                for filename in os.listdir(out_dir):
                    zip_out.write(os.path.join(out_dir, filename), arcname=filename)

            await interaction.followup.send(
                "Here is your flattened zip file.",
                file=discord.File(out_zip, filename="flattened.zip"),
                ephemeral=True,
            )


async def setup(bot):
    await bot.add_cog(FlattenZip(bot))
