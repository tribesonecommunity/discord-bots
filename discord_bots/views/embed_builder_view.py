from typing import Any, Dict, List, Optional, Union

import discord
from discord import Colour, Embed, Interaction

from discord_bots.views.base import BaseView

# Type alias for channels that can send messages
SendableChannel = Union[
    discord.TextChannel,
    discord.DMChannel,
    discord.GroupChannel,
    discord.Thread,
    discord.VoiceChannel,
    discord.StageChannel,
]


# Discord embed limits
EMBED_TITLE_MAX_LENGTH = 256
EMBED_DESCRIPTION_MAX_LENGTH = 2048
EMBED_FIELD_NAME_MAX_LENGTH = 256
EMBED_FIELD_VALUE_MAX_LENGTH = 1024
EMBED_FOOTER_MAX_LENGTH = 2048
INLINE_INPUT_MAX_LENGTH = 5

# UI Layout
BUTTON_ROW_BASIC = 0  # Title, Description
BUTTON_ROW_FIELDS = 1  # Add, Remove, Edit Field
BUTTON_ROW_MEDIA = 2  # Set Thumbnail, Set Image, Set Footer
BUTTON_ROW_ACTIONS = 3  # Set Color, Send Embed

# Error messages
ERROR_NO_CHANNEL = "❌ Unable to access channel!"
ERROR_INVALID_CHANNEL_TYPE = "❌ Cannot send embeds in this channel type!"
ERROR_INVALID_INLINE_VALUE = "❌ Invalid inline value! Please enter: true or false."

# Success messages
SUCCESS_EMBED_SENT = "Embed sent!"

# Field management
FIELD_VALUE_PREVIEW_LENGTH = 50


# ============================================================================
# MAIN EMBED BUILDER VIEW
# ============================================================================


class EmbedBuilderView(BaseView):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.title: str = "Title"
        self.description: str = "Description"
        self.colour: Colour = Colour.blurple()
        self.thumbnail_url: Optional[str] = None
        self.image_url: Optional[str] = None
        self.footer_text: Optional[str] = None
        self.fields: List[Dict[str, Any]] = (
            []
        )  # List of dicts: {"name": str, "value": str, "inline": bool}
        self.original_message: Optional[discord.Message] = (
            None  # Reference to the original embed builder message
        )
        self._active_selection: Optional[discord.Message] = (
            None  # Track active selection window
        )

    async def _close_active_selection(self):
        """Close any active selection window."""
        if self._active_selection:
            try:
                await self._active_selection.delete()
            except discord.NotFound:
                pass  # Message was already deleted
            except discord.HTTPException:
                pass  # Failed to delete, but continue
            finally:
                self._active_selection = None

    async def _update_embed_and_cleanup_selection(self, interaction: Interaction):
        """Common pattern: clear active selection, defer response, update embed, delete response."""
        # Clear active selection reference (since we're about to delete it)
        self._active_selection = None

        # Defer the response first
        await interaction.response.defer()

        # Update the original embed builder message
        if self.original_message:
            await self.original_message.edit(embed=self.create_embed(), view=self)

        # Delete the response to keep interface clean
        await interaction.delete_original_response()

    def _parse_inline_input(self, value: str) -> bool:
        normalized_value = value.strip().lower()
        if normalized_value == "true":
            return True
        elif normalized_value == "false":
            return False
        else:
            raise ValueError(f"Invalid input: '{value}'. Expected 'true' or 'false'.")

    def create_embed(self) -> Embed:
        embed = Embed(
            title=self.title, description=self.description, colour=self.colour
        )

        if self.thumbnail_url:
            embed.set_thumbnail(url=self.thumbnail_url)

        if self.image_url:
            embed.set_image(url=self.image_url)

        if self.footer_text:
            embed.set_footer(text=self.footer_text)

        for field in self.fields:
            embed.add_field(
                name=field["name"],
                value=field["value"],
                inline=field.get("inline", True),
            )
        return embed

    @discord.ui.button(
        label="Set Title", style=discord.ButtonStyle.primary, row=BUTTON_ROW_BASIC
    )
    async def set_title(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SetTitleModal(self))

    @discord.ui.button(
        label="Set Description", style=discord.ButtonStyle.primary, row=BUTTON_ROW_BASIC
    )
    async def set_description(
        self, interaction: Interaction, button: discord.ui.Button
    ):
        await interaction.response.send_modal(SetDescriptionModal(self))

    def _create_field_options(self) -> List[discord.SelectOption]:
        """Create SelectOption list for field selection with value previews."""
        return [
            discord.SelectOption(
                label=field["name"],
                value=str(i),
                description=(
                    field["value"][:FIELD_VALUE_PREVIEW_LENGTH] + "..."
                    if len(field["value"]) > FIELD_VALUE_PREVIEW_LENGTH
                    else field["value"]
                ),
            )
            for i, field in enumerate(self.fields)
        ]

    async def _send_field_selector(self, interaction: Interaction, selector_class):
        """Common logic for sending field selection dropdowns."""
        if not self.fields:
            action = (
                "edit" if selector_class.__name__ == "EditFieldSelect" else "remove"
            )
            await interaction.response.send_message(
                f"No fields to {action}.", ephemeral=True
            )
            return

        view = discord.ui.View()
        view.add_item(selector_class(self))

        # Close any existing selection and send new one
        await self._close_active_selection()
        await interaction.response.send_message(view=view, ephemeral=True)
        self._active_selection = await interaction.original_response()

    @discord.ui.button(
        label="Add Field", style=discord.ButtonStyle.success, row=BUTTON_ROW_FIELDS
    )
    async def add_field(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddFieldModal(self))

    @discord.ui.button(
        label="Remove Field", style=discord.ButtonStyle.danger, row=BUTTON_ROW_FIELDS
    )
    async def remove_field(self, interaction: Interaction, button: discord.ui.Button):
        await self._send_field_selector(interaction, RemoveFieldSelect)

    @discord.ui.button(
        label="Edit Field", style=discord.ButtonStyle.secondary, row=BUTTON_ROW_FIELDS
    )
    async def edit_field(self, interaction: Interaction, button: discord.ui.Button):
        await self._send_field_selector(interaction, EditFieldSelect)

    @discord.ui.button(
        label="Set Thumbnail", style=discord.ButtonStyle.secondary, row=BUTTON_ROW_MEDIA
    )
    async def set_thumbnail(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SetThumbnailModal(self))

    @discord.ui.button(
        label="Set Image", style=discord.ButtonStyle.secondary, row=BUTTON_ROW_MEDIA
    )
    async def set_image(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SetImageModal(self))

    @discord.ui.button(
        label="Set Footer", style=discord.ButtonStyle.secondary, row=BUTTON_ROW_MEDIA
    )
    async def set_footer(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SetFooterModal(self))

    @discord.ui.button(
        label="Set Color", style=discord.ButtonStyle.secondary, row=BUTTON_ROW_ACTIONS
    )
    async def set_color(self, interaction: Interaction, button: discord.ui.Button):
        # Close any existing selection first
        await self._close_active_selection()

        view = discord.ui.View()
        # Add all color category dropdowns
        view.add_item(BasicColorSelect(self))
        view.add_item(DarkColorSelect(self))
        view.add_item(GrayColorSelect(self))
        view.add_item(SpecialColorSelect(self))

        await interaction.response.send_message(
            content=(
                "Select an embed color from any category below:\n"
                "- **Basic Colors**: Blue, Red, Green, etc.\n"
                "- **Dark Colors**: Dark variants of basic colors\n"
                "- **Gray Colors**: Light to dark gray shades\n"
                "- **Special Colors**: Discord brand colors, themes, and random"
            ),
            view=view,
            ephemeral=True,
        )
        self._active_selection = await interaction.original_response()

    @discord.ui.button(
        label="Send Embed", style=discord.ButtonStyle.success, row=BUTTON_ROW_ACTIONS
    )
    async def send_embed(self, interaction: Interaction, button: discord.ui.Button):
        embed = self.create_embed()

        # Type check the channel to ensure it can send messages
        if not interaction.channel:
            await interaction.response.send_message(ERROR_NO_CHANNEL, ephemeral=True)
            return

        if isinstance(
            interaction.channel, (discord.ForumChannel, discord.CategoryChannel)
        ):
            await interaction.response.send_message(
                ERROR_INVALID_CHANNEL_TYPE, ephemeral=True
            )
            return

        # Safe to send message now
        channel: SendableChannel = interaction.channel
        await channel.send(embed=embed)
        await interaction.response.send_message(SUCCESS_EMBED_SENT, ephemeral=True)


# ============================================================================
# FIELD SELECTOR CLASSES
# ============================================================================


# Dropdown for selecting a field to edit
class EditFieldSelect(discord.ui.Select):
    def __init__(self, builder_view: EmbedBuilderView):
        super().__init__(
            placeholder="Select a field to edit...",
            options=builder_view._create_field_options(),
        )
        self.builder_view = builder_view

    async def callback(self, interaction: Interaction):
        index = int(self.values[0])
        field = self.builder_view.fields[index]
        await interaction.response.send_modal(
            EditFieldModal(self.builder_view, index, field)
        )


# Dropdown for selecting a field to remove
class RemoveFieldSelect(discord.ui.Select):
    def __init__(self, builder_view: EmbedBuilderView):
        super().__init__(
            placeholder="Select a field to remove...",
            options=builder_view._create_field_options(),
        )
        self.builder_view = builder_view

    async def callback(self, interaction: Interaction):
        index = int(self.values[0])
        # Remove the field
        self.builder_view.fields.pop(index)

        # Use common cleanup pattern
        await self.builder_view._update_embed_and_cleanup_selection(interaction)


# ============================================================================
# COLOR SELECTOR CLASSES
# ============================================================================


# Base class for color selection dropdowns
class BaseColorSelect(discord.ui.Select):
    color_options = []  # Override in subclasses

    def __init__(self, builder_view: EmbedBuilderView, placeholder: str):
        options = [
            discord.SelectOption(label=name, value=value)
            for name, value, _ in self.color_options
        ]
        super().__init__(placeholder=placeholder, options=options)
        self.builder_view = builder_view
        self.color_map = {value: color for _, value, color in self.color_options}

    async def callback(self, interaction: Interaction):
        selected_color_key = self.values[0]
        selected_color = self.color_map[selected_color_key]

        # Update embed color
        self.builder_view.colour = selected_color

        # Use common cleanup pattern
        await self.builder_view._update_embed_and_cleanup_selection(interaction)


# Basic colors dropdown (11 colors)
class BasicColorSelect(BaseColorSelect):
    color_options = [
        ("Blue", "blue", discord.Colour.blue()),
        ("Green", "green", discord.Colour.green()),
        ("Red", "red", discord.Colour.red()),
        ("Orange", "orange", discord.Colour.orange()),
        ("Yellow", "yellow", discord.Colour.yellow()),
        ("Purple", "purple", discord.Colour.purple()),
        ("Pink", "pink", discord.Colour.pink()),
        ("Magenta", "magenta", discord.Colour.magenta()),
        ("Fuchsia", "fuchsia", discord.Colour.fuchsia()),
        ("Gold", "gold", discord.Colour.gold()),
        ("Teal", "teal", discord.Colour.teal()),
    ]

    def __init__(self, builder_view: EmbedBuilderView):
        super().__init__(builder_view, "Basic Colors...")


# Dark colors dropdown (8 colors)
class DarkColorSelect(BaseColorSelect):
    color_options = [
        ("Dark Blue", "dark_blue", discord.Colour.dark_blue()),
        ("Dark Green", "dark_green", discord.Colour.dark_green()),
        ("Dark Red", "dark_red", discord.Colour.dark_red()),
        ("Dark Orange", "dark_orange", discord.Colour.dark_orange()),
        ("Dark Purple", "dark_purple", discord.Colour.dark_purple()),
        ("Dark Magenta", "dark_magenta", discord.Colour.dark_magenta()),
        ("Dark Gold", "dark_gold", discord.Colour.dark_gold()),
        ("Dark Teal", "dark_teal", discord.Colour.dark_teal()),
    ]

    def __init__(self, builder_view: EmbedBuilderView):
        super().__init__(builder_view, "Dark Colors...")


# Gray colors dropdown (4 colors - removed redundant grey/gray duplicates)
class GrayColorSelect(BaseColorSelect):
    color_options = [
        ("Light Gray", "light_gray", discord.Colour.light_gray()),
        ("Dark Gray", "dark_gray", discord.Colour.dark_gray()),
        ("Lighter Gray", "lighter_gray", discord.Colour.lighter_gray()),
        ("Darker Gray", "darker_gray", discord.Colour.darker_gray()),
    ]

    def __init__(self, builder_view: EmbedBuilderView):
        super().__init__(builder_view, "Gray Colors...")


# Special colors dropdown (15 colors - Discord brand + theme colors + random)
class SpecialColorSelect(BaseColorSelect):
    color_options = [
        # Discord brand colors
        ("Blurple", "blurple", discord.Colour.blurple()),
        ("Old Blurple", "og_blurple", discord.Colour.og_blurple()),
        ("Greyple", "greyple", discord.Colour.greyple()),
        ("Brand Green", "brand_green", discord.Colour.brand_green()),
        ("Brand Red", "brand_red", discord.Colour.brand_red()),
        # Theme colors
        ("Default", "default", discord.Colour.default()),
        ("Light Theme", "light_theme", discord.Colour.light_theme()),
        ("Dark Theme", "dark_theme", discord.Colour.dark_theme()),
        ("Light Embed", "light_embed", discord.Colour.light_embed()),
        ("Dark Embed", "dark_embed", discord.Colour.dark_embed()),
        ("Ash Theme", "ash_theme", discord.Colour.ash_theme()),
        ("Ash Embed", "ash_embed", discord.Colour.ash_embed()),
        ("Onyx Theme", "onyx_theme", discord.Colour.onyx_theme()),
        ("Onyx Embed", "onyx_embed", discord.Colour.onyx_embed()),
        # Special
        ("Random", "random", discord.Colour.random()),
    ]

    def __init__(self, builder_view: EmbedBuilderView):
        super().__init__(builder_view, "Special Colors...")


# ============================================================================
# MODAL CLASSES
# ============================================================================


class SetTitleModal(discord.ui.Modal, title="Set Embed Title"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.title_input = discord.ui.TextInput(
            label="Title",
            default=view.title,
            required=False,
            max_length=EMBED_TITLE_MAX_LENGTH,
        )
        self.add_item(self.title_input)

    async def on_submit(self, interaction: Interaction):
        self.view.title = self.title_input.value
        await interaction.response.edit_message(
            embed=self.view.create_embed(), view=self.view
        )


class SetDescriptionModal(discord.ui.Modal, title="Set Embed Description"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.desc_input = discord.ui.TextInput(
            label="Description",
            default=view.description,
            required=False,
            max_length=EMBED_DESCRIPTION_MAX_LENGTH,
            style=discord.TextStyle.long,
        )
        self.add_item(self.desc_input)

    async def on_submit(self, interaction: Interaction):
        self.view.description = self.desc_input.value
        await interaction.response.edit_message(
            embed=self.view.create_embed(), view=self.view
        )


class AddFieldModal(discord.ui.Modal, title="Add Embed Field"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.name_input = discord.ui.TextInput(
            label="Field Name", required=True, max_length=EMBED_FIELD_NAME_MAX_LENGTH
        )
        self.value_input = discord.ui.TextInput(
            label="Field Value",
            required=True,
            max_length=EMBED_FIELD_VALUE_MAX_LENGTH,
            style=discord.TextStyle.long,
        )
        self.inline_input = discord.ui.TextInput(
            label="Inline? (true/false)",
            default="true",
            required=True,
            max_length=INLINE_INPUT_MAX_LENGTH,
        )
        self.add_item(self.name_input)
        self.add_item(self.value_input)
        self.add_item(self.inline_input)

    async def on_submit(self, interaction: Interaction):
        # Parse inline input
        try:
            inline_value = self.view._parse_inline_input(self.inline_input.value)
        except ValueError:
            await interaction.response.send_message(
                ERROR_INVALID_INLINE_VALUE, ephemeral=True
            )
            return

        self.view.fields.append(
            {
                "name": self.name_input.value,
                "value": self.value_input.value,
                "inline": inline_value,
            }
        )
        await interaction.response.edit_message(
            embed=self.view.create_embed(), view=self.view
        )


class EditFieldModal(discord.ui.Modal, title="Edit Embed Field"):
    view: EmbedBuilderView
    index: int
    name_input: discord.ui.TextInput
    value_input: discord.ui.TextInput
    inline_input: discord.ui.TextInput

    def __init__(self, view: EmbedBuilderView, index: int, field: dict) -> None:
        super().__init__()
        self.view: EmbedBuilderView = view
        self.index: int = index
        self.name_input: discord.ui.TextInput = discord.ui.TextInput(
            label="Field Name",
            default=field["name"],
            required=True,
            max_length=EMBED_FIELD_NAME_MAX_LENGTH,
        )
        self.value_input: discord.ui.TextInput = discord.ui.TextInput(
            label="Field Value",
            default=field["value"],
            required=True,
            max_length=EMBED_FIELD_VALUE_MAX_LENGTH,
            style=discord.TextStyle.long,
        )
        self.inline_input: discord.ui.TextInput = discord.ui.TextInput(
            label="Inline? (true/false)",
            default=str(field.get("inline", True)).lower(),
            required=True,
            max_length=INLINE_INPUT_MAX_LENGTH,
        )
        self.add_item(self.name_input)
        self.add_item(self.value_input)
        self.add_item(self.inline_input)

    async def on_submit(self, interaction: Interaction):
        # Parse inline input
        try:
            inline_value = self.view._parse_inline_input(self.inline_input.value)
        except ValueError:
            await interaction.response.send_message(
                ERROR_INVALID_INLINE_VALUE, ephemeral=True
            )
            return

        self.view.fields[self.index] = {
            "name": self.name_input.value,
            "value": self.value_input.value,
            "inline": inline_value,
        }

        # Use common cleanup pattern
        await self.view._update_embed_and_cleanup_selection(interaction)


class SetThumbnailModal(discord.ui.Modal, title="Set Embed Thumbnail"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.thumbnail_input = discord.ui.TextInput(
            label="Thumbnail URL",
            placeholder="https://example.com/image.png",
            default=view.thumbnail_url or "",
            required=False,
            style=discord.TextStyle.short,
        )
        self.add_item(self.thumbnail_input)

    async def on_submit(self, interaction: Interaction):
        # Set thumbnail URL (empty string means remove thumbnail)
        self.view.thumbnail_url = self.thumbnail_input.value.strip() or None
        await interaction.response.edit_message(
            embed=self.view.create_embed(), view=self.view
        )


class SetImageModal(discord.ui.Modal, title="Set Embed Image"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.image_input = discord.ui.TextInput(
            label="Image URL",
            placeholder="https://example.com/image.png",
            default=view.image_url or "",
            required=False,
            style=discord.TextStyle.short,
        )
        self.add_item(self.image_input)

    async def on_submit(self, interaction: Interaction):
        # Set image URL (empty string means remove image)
        self.view.image_url = self.image_input.value.strip() or None
        await interaction.response.edit_message(
            embed=self.view.create_embed(), view=self.view
        )


class SetFooterModal(discord.ui.Modal, title="Set Embed Footer"):
    def __init__(self, view: EmbedBuilderView):
        super().__init__()
        self.view = view
        self.footer_input = discord.ui.TextInput(
            label="Footer Text",
            placeholder="Enter footer text...",
            default=view.footer_text or "",
            required=False,
            max_length=EMBED_FOOTER_MAX_LENGTH,
            style=discord.TextStyle.long,
        )
        self.add_item(self.footer_input)

    async def on_submit(self, interaction: Interaction):
        # Set footer text (empty string means remove footer)
        self.view.footer_text = self.footer_input.value.strip() or None
        await interaction.response.edit_message(
            embed=self.view.create_embed(), view=self.view
        )
