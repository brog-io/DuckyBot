import discord
from discord.ext import commands
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import re


class LinkCleaner(commands.Cog):
    """Removes tracking parameters from URLs posted in chat, but skips Discord media/CDN links."""

    DISCORD_MEDIA_HOSTS = {"media.discordapp.net", "cdn.discordapp.com"}

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.tracking_params = [
            # Google Analytics & Ads
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "utm_referrer",
            "gclid",
            "gclsrc",
            "dclid",
            "_ga",
            "ga_source",
            "ga_medium",
            "ga_campaign",
            "ga_term",
            "ga_content",
            # Facebook & Meta
            "fbclid",
            "fb_action_ids",
            "fb_action_types",
            "fb_ref",
            "fb_source",
            "fbref",
            "fref",
            "hc_ref",
            "fb_comment_id",
            # Twitter/X
            "ref_src",
            "ref_url",
            "s",
            "t",
            "twclid",
            "tweetbutton",
            "related",
            "original_referer",
            "tw_i",
            "tw_p",
            # Amazon
            "tag",
            "linkCode",
            "linkId",
            "ascsubtag",
            "creativeASIN",
            "creative",
            "camp",
            "adid",
            "ref_",
            "ref",
            "pf_rd_r",
            "pf_rd_p",
            "pf_rd_m",
            "pf_rd_s",
            "pf_rd_t",
            "pf_rd_i",
            "keywords",
            "sprefix",
            # YouTube & Google
            "si",
            "feature",
            "kw",
            "gws_rd",
            "ei",
            "ved",
            "usg",
            "sa",
            "sqi",
            "biw",
            "bih",
            "source",
            "client",
            # LinkedIn
            "trackingId",
            "lipi",
            "trk",
            "trkInfo",
            "li_fat_id",
            "li_source",
            "li_medium",
            "li_campaign",
            "originalSubdomain",
            # TikTok
            "is_from_webapp",
            "sender_device",
            "sender_web_id",
            "is_copy_url",
            "checksum",
            "tt_from",
            "foryou",
            # Instagram
            "igshid",
            "igsh",
            "hl",
            "taken-by",
            # Pinterest
            "source_app",
            "data",
            # Microsoft/Bing
            "msclkid",
            "cvid",
            "FORM",
            "sk",
            "sp",
            "sc",
            "qs",
            "qpvt",
            "FPIG",
            # Mailchimp
            "mc_cid",
            "mc_eid",
            # HubSpot
            "hsa_acc",
            "hsa_cam",
            "hsa_grp",
            "hsa_ad",
            "hsa_src",
            "hsa_tgt",
            "hsa_kw",
            "hsa_mt",
            "hsa_net",
            "hsa_ver",
            "_hsenc",
            "_hsmi",
            # Salesforce
            "sfmc_sub",
            "sfmc_activityid",
            "sfmc_journey",
            "sfmc_j",
            "et_rid",
            "et_cid",
            # Adobe
            "sc_cid",
            "sc_lid",
            "sc_uid",
            "s_cid",
            # General affiliate/tracking
            "aff_id",
            "aff_sub",
            "aff_sub2",
            "aff_sub3",
            "aff_sub4",
            "aff_sub5",
            "aff_click_id",
            "affiliate_id",
            "click_id",
            "clickid",
            "campaign_id",
            "ad_id",
            "adset_id",
            "placement_id",
            "creative_id",
            "network_id",
            "publisher_id",
            "site_id",
            "banner_id",
            "keyword",
            "matchtype",
            "device",
            "adposition",
            "target",
            "targetid",
            "loc_interest_ms",
            "loc_physical_ms",
            "feeditemid",
            "gbraid",
            "wbraid",
            # Referrer tracking
            "referrer",
            "referer",
            "sref",
            "ref",
            "r",
            "source",
            "src",
            "from",
            "rurl",
            "return_to",
            "redirect",
            "next",
            "continue",
            # Session/user tracking
            "sid",
            "sessionid",
            "session_id",
            "ssid",
            "user_id",
            "uid",
            "userid",
            "visitor_id",
            "vid",
            "cid",
            "client_id",
            "customerid",
            "_branch_match_id",
            # Campaign tracking
            "track_id",
            "tracking_id",
            "trackingid",
            "tid",
            "campaign",
            "promo",
            "promocode",
            "coupon",
            "discount",
            "offer",
            "deal",
            "sale",
            # Social sharing
            "share",
            "shared",
            "via",
            "share_id",
            "social",
            "social_type",
            "share_type",
            "recruiter",
            "invited_by",
            # Email tracking
            "email_source",
            "email_campaign",
            "newsletter",
            "mailer_id",
            "list_id",
            "subscriber_id",
            "message_id",
            "link_id",
            "recipient",
            # Mobile app tracking
            "app",
            "platform",
            "version",
            "build",
            "install_id",
            "advertising_id",
            "idfa",
            "idfv",
            "android_id",
            "gps_adid",
            # Analytics platforms
            "pk_source",
            "pk_medium",
            "pk_campaign",
            "pk_content",
            "pk_cid",  # Piwik/Matomo
            "yclid",  # Yandex
            "zanpid",  # Zanox
            "adfox_event_id",  # AdFox
            "vero_conv",
            "vero_id",  # Vero
            "nr_email_referer",  # New Relic
            "goalid",
            "gsessionid",  # GoSquared
            "_openstat",  # OpenStat
            "ito",
            "itm_source",
            "itm_medium",
            "itm_campaign",  # Internal tracking
            # Generic parameters
            "tag",
            "tags",
            "label",
            "labels",
            "category",
            "type",
            "subtype",
            "subid",
            "subid1",
            "subid2",
            "subid3",
            "subid4",
            "subid5",
            "var",
            "var1",
            "var2",
            "var3",
            "variable",
            "custom",
            "extra",
            "data",
            "info",
            "details",
            "meta",
            "context",
            "state",
            "status",
            "action",
            "event",
            "trigger",
            "method",
            "channel",
            "medium",
            "format",
            "content_type",
            "referral",
            "invitation",
            "invite_code",
            # Time-based tracking
            "timestamp",
            "ts",
            "time",
            "date",
            "when",
            "at",
            "moment",
            "created",
            "modified",
            "updated",
            "visited",
            "clicked",
            "viewed",
            # Location tracking
            "location",
            "loc",
            "geo",
            "country",
            "region",
            "city",
            "zip",
            "lat",
            "lng",
            "latitude",
            "longitude",
            "coords",
            "position",
            # Device/browser tracking
            "browser",
            "user_agent",
            "ua",
            "device_type",
            "screen",
            "resolution",
            "mobile",
            "tablet",
            "desktop",
            "os",
            "operating_system",
        ]

        self.url_pattern = re.compile(r"https?://\S+")

    def is_valid_url(self, url: str) -> bool:
        """Returns True if the string is a valid URL."""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc])
        except Exception:
            return False

    def clean_url(self, url: str) -> str:
        """Removes tracking parameters from a URL, but skips Discord media/CDN URLs."""
        try:
            parsed = urlparse(url)

            # Skip cleaning for Discord media/CDN URLs
            if parsed.netloc in self.DISCORD_MEDIA_HOSTS:
                return url

            params = parse_qs(parsed.query)
            cleaned = {k: v for k, v in params.items() if k not in self.tracking_params}
            query = urlencode(cleaned, doseq=True)

            return urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    query,
                    parsed.fragment,
                )
            )
        except Exception:
            return url

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        urls = self.url_pattern.findall(message.content)
        cleaned_links = []

        for url in urls:
            url = url.rstrip(".,!?)")
            if self.is_valid_url(url):
                cleaned = self.clean_url(url)
                if cleaned != url:
                    cleaned_links.append(cleaned)

        if cleaned_links:
            view = discord.ui.View()
            for link in cleaned_links:
                view.add_item(discord.ui.Button(label="Open Cleaned Link", url=link))

            await message.reply(
                "Here are the cleaned links without tracking parameters:",
                mention_author=False,
                view=view,
            )

        await self.bot.process_commands(message)


async def setup(bot):
    await bot.add_cog(LinkCleaner(bot))
