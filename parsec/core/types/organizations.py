# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS
import attr
from pendulum import DateTime


@attr.s(frozen=True, slots=True, auto_attribs=True)
class OrganizationStats:
    users: int
    data_size: int
    metadata_size: int


@attr.s(frozen=True, slots=True, auto_attribs=True)
class OrganizationStatus:
    expiration_date: DateTime
    outsider_enabled: bool
