-- Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2016-2021 Scille SAS


-------------------------------------------------------
--  Migration
-------------------------------------------------------


CREATE TABLE migration (
    _id INTEGER PRIMARY KEY,
    name VARCHAR(256) NOT NULL UNIQUE,
    applied TIMESTAMPTZ NOT NULL
);
