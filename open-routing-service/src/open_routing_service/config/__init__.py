"""Configuration package — re-exports the settings singleton."""

from open_routing_service.config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
