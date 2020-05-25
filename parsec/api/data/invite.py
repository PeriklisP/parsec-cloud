# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import re
from typing import Optional, Tuple, List
from random import randint, shuffle

from parsec.crypto import VerifyKey, PublicKey, PrivateKey, SecretKey
from parsec.serde import fields, post_load
from parsec.api.protocol import (
    DeviceID,
    DeviceIDField,
    DeviceName,
    DeviceNameField,
    HumanHandle,
    HumanHandleField,
)
from parsec.api.data.base import BaseAPIData, BaseSchema


class SASCode(str):
    __slots__ = ()
    length = 4
    symbols = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    regex = re.compile(rf"^[{symbols}]{{{length}}}$")

    def __init__(self, raw):
        if not isinstance(raw, str) or not self.regex.match(raw):
            raise ValueError("Invalid SAS code")

    def __repr__(self):
        return f"<OrganizationID {super().__repr__()}>"

    @classmethod
    def from_int(cls, num):
        if num < 0:
            raise ValueError("Provided integer is negative")
        result = ""
        for _ in range(cls.length):
            result += cls.symbols[num % len(cls.symbols)]
            num //= len(cls.symbols)
        if num != 0:
            raise ValueError("Provided integer is too large")
        return cls(result)


def generate_sas_codes(
    claimer_nonce: bytes, greeter_nonce: bytes, shared_secret_key: SecretKey
) -> Tuple[SASCode, SASCode]:
    # Computes combined HMAC
    combined_nonce = claimer_nonce + greeter_nonce
    # Digest size of 5 bytes so we can split it beween two 20bits SAS
    combined_hmac = shared_secret_key.hmac(combined_nonce, digest_size=5)

    hmac_as_int = int.from_bytes(combined_hmac, "big")
    # Big endian number extracted from bits [0, 20[
    claimer_sas = hmac_as_int % 2 ** 20
    # Big endian number extracted from bits [20, 40[
    greeter_sas = (hmac_as_int >> 20) % 2 ** 20

    return SASCode.from_int(claimer_sas), SASCode.from_int(greeter_sas)


def generate_sas_code_candidates(valid_sas: SASCode, size: int = 3) -> List[SASCode]:
    candidates = {valid_sas}
    while len(candidates) < size:
        candidates.add(SASCode.from_int(randint(0, 2 ** 20 - 1)))
    ordered_candidates = list(candidates)
    shuffle(ordered_candidates)
    return ordered_candidates


class InviteUserData(BaseAPIData):
    class SCHEMA_CLS(BaseSchema):
        type = fields.CheckedConstant("invite_user_data", required=True)
        # Claimer ask for device_id/human_handle, but greeter has final word on this
        requested_device_id = DeviceIDField(required=True)
        requested_human_handle = HumanHandleField(allow_none=True, missing=None)
        # Note claiming user also imply creating a first device
        public_key = fields.PublicKey(required=True)
        verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return InviteUserData(**data)

    requested_device_id: DeviceID
    requested_human_handle: Optional[HumanHandle]
    public_key: PublicKey
    verify_key: VerifyKey


class InviteUserConfirmation(BaseAPIData):
    class SCHEMA_CLS(BaseSchema):
        type = fields.CheckedConstant("invite_user_confirmation", required=True)
        device_id = DeviceIDField(required=True)
        human_handle = HumanHandleField(allow_none=True, missing=None)
        is_admin = fields.Boolean(required=True)
        root_verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return InviteUserConfirmation(**data)

    device_id: DeviceID
    human_handle: Optional[HumanHandle]
    is_admin: bool
    root_verify_key: VerifyKey


class InviteDeviceData(BaseAPIData):
    class SCHEMA_CLS(BaseSchema):
        type = fields.CheckedConstant("invite_device_data", required=True)
        # Claimer ask for device_name, but greeter has final word on this
        requested_device_name = DeviceNameField(required=True)
        verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return InviteDeviceData(**data)

    requested_device_name: DeviceName
    verify_key: VerifyKey


class InviteDeviceConfirmation(BaseAPIData):
    class SCHEMA_CLS(BaseSchema):
        type = fields.CheckedConstant("invite_device_confirmation", required=True)
        device_id = DeviceIDField(required=True)
        human_handle = HumanHandleField(allow_none=True, missing=None)
        is_admin = fields.Boolean(required=True)
        private_key = fields.PrivateKey(required=True)
        root_verify_key = fields.VerifyKey(required=True)

        @post_load
        def make_obj(self, data):
            data.pop("type")
            return InviteDeviceConfirmation(**data)

    device_id: DeviceID
    human_handle: Optional[HumanHandle]
    is_admin: bool
    private_key: PrivateKey
    root_verify_key: VerifyKey
