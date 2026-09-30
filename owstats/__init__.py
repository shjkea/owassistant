# -*- coding: utf-8 -*-
"""OW 国服战绩直连后端（无 localhost 服务）。"""
from .auth import DashenQRAuth
from .credential_store import CredentialStore
from .errors import AuthSessionError, MissingCredentialError, OWStatsError, PlayerNotFoundError, UpstreamError
from .models import DashenCredential, QRStart, QRState, ResolvedPlayer, SignRequest
from .service import OWStatsService

__all__ = [
    "AuthSessionError",
    "CredentialStore",
    "DashenCredential",
    "DashenQRAuth",
    "MissingCredentialError",
    "OWStatsError",
    "OWStatsService",
    "PlayerNotFoundError",
    "QRStart",
    "QRState",
    "ResolvedPlayer",
    "SignRequest",
    "UpstreamError",
]

from .localization import (
    localize_game_mode, localize_hero, localize_instance_type,
    localize_map, localize_rank, localize_role,
)
