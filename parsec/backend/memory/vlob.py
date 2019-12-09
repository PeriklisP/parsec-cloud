# Parsec Cloud (https://parsec.cloud) Copyright (c) AGPLv3 2019 Scille SAS

import attr
import pendulum
from uuid import UUID
from datetime import datetime
from typing import List, Tuple, Dict, Optional
from collections import defaultdict

from parsec.api.protocol import DeviceID, OrganizationID
from parsec.api.protocol import RealmRole
from parsec.backend.realm import BaseRealmComponent, RealmNotFoundError
from parsec.backend.vlob import (
    BaseVlobComponent,
    VlobAccessError,
    VlobVersionError,
    VlobTimestampError,
    VlobNotFoundError,
    VlobAlreadyExistsError,
    VlobEncryptionRevisionError,
    VlobInMaintenanceError,
    VlobNotInMaintenanceError,
)


@attr.s
class Vlob:
    realm_id: UUID = attr.ib()
    data: List[Tuple[bytes, DeviceID, pendulum.Pendulum, pendulum.Pendulum]] = attr.ib(factory=list)

    @property
    def current_version(self):
        return len(self.data)


class RealmTask:
    def __init__(self, realm_id, vlobs):
        self.realm_id = realm_id
        self._original_vlobs = vlobs
        self._done = {}
        self._todo = self._init_todo(vlobs)
        self._total = len(self._todo)

    def _init_todo(self, vlobs):
        raise NotImplementedError("subclass must override this method")

    def get_vlobs(self):
        raise NotImplementedError("subclass must override this method")

    def is_finished(self):
        return not self._todo


class GarbageCollection(RealmTask):
    def _init_todo(self, vlobs):
        todo = {}
        for vlob_id, vlob in vlobs.items():
            for version, (_, _, created_at, to_quarantine) in enumerate(vlob.data):
                if to_quarantine is None:
                    try:
                        todo[vlob_id].append((version, created_at))
                    except KeyError:
                        todo[vlob_id] = [(version, created_at)]
        return todo

    def get_one_vlob(self):
        for vlob_id, timesdata in self._todo.items():
            if vlob_id in self._done:
                continue
            return (vlob_id, timesdata)

    def save_vlob(self, vlob_id, versions_to_erase):
        if vlob_id not in self._done:
            try:
                del self._todo[vlob_id]
            except KeyError:
                raise VlobNotFoundError()
            else:
                self._done[vlob_id] = versions_to_erase
        return self._total, len(self._done)

    def get_vlobs(self):
        assert self.is_finished()
        vlobs = {}
        now = pendulum.now()
        for vlob_id, versions_to_erase in sorted(self._done.items()):
            try:
                vlob = self._original_vlobs[vlob_id]
            except KeyError:
                raise VlobNotFoundError()
            else:
                data = []
                for version, _ in enumerate(vlob.data):
                    (data, author, timestamp, to_quarantine) = vlob.data[version]
                    if version in versions_to_erase:
                        to_quarantine = now
                    if vlob_id not in vlobs:
                        vlobs[vlob_id] = Vlob(
                            self.realm_id, [(data, author, timestamp, to_quarantine)]
                        )
                    else:
                        vlobs[vlob_id].data.append((data, author, timestamp, to_quarantine))
        return vlobs


class Reencryption(RealmTask):
    def _init_todo(self, vlobs):
        todo = {}
        for vlob_id, vlob in vlobs.items():
            for index, (data, _, _, _) in enumerate(vlob.data):
                version = index + 1
                todo[(vlob_id, version)] = data
        return todo

    def get_batch(self, size):
        batch = []
        for (vlob_id, version), data in self._todo.items():
            if (vlob_id, version) in self._done:
                continue
            batch.append((vlob_id, version, data))
        return batch[:size]

    def save_batch(self, batch):
        for vlob_id, version, data in batch:
            key = (vlob_id, version)
            if key in self._done:
                continue
            try:
                del self._todo[key]
            except KeyError:
                raise VlobNotFoundError()
            self._done[key] = data

        return self._total, len(self._done)

    def get_vlobs(self):
        assert self.is_finished()
        vlobs = {}
        for (vlob_id, version), data in sorted(self._done.items()):
            try:
                (_, author, timestamp, to_quarantine) = self._original_vlobs[vlob_id].data[
                    version - 1
                ]

            except KeyError:
                raise VlobNotFoundError()

            if vlob_id not in vlobs:
                vlobs[vlob_id] = Vlob(self.realm_id, [(data, author, timestamp, to_quarantine)])
            else:
                vlobs[vlob_id].data.append((data, author, timestamp, to_quarantine))
            assert len(vlobs[vlob_id].data) == version

        return vlobs


@attr.s
class Changes:
    checkpoint: int = attr.ib(default=0)
    changes: Dict[UUID, Tuple[DeviceID, int, int]] = attr.ib(factory=dict)
    reencryption: Reencryption = attr.ib(default=None)
    garbage_collection: GarbageCollection = attr.ib(default=None)


class MemoryVlobComponent(BaseVlobComponent):
    def __init__(self, send_event):
        self._send_event = send_event
        self._realm_component = None
        self._vlobs = {}
        self._per_realm_changes = defaultdict(Changes)

    def register_components(self, realm: BaseRealmComponent, **other_components):
        self._realm_component = realm

    def _get_changes_to_maintenance_start_hook(self, organization_id, realm_id):
        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert not changes.reencryption
        assert not changes.garbage_collection
        return changes

    def _get_realm_vlobs(self, organization_id, realm_id):
        return {
            vlob_id: vlob
            for (orgid, vlob_id), vlob in self._vlobs.items()
            if orgid == organization_id and vlob.realm_id == realm_id
        }

    def _maintenance_start_hook(self, attr, Task, organization_id, realm_id):
        changes = self._get_changes_to_maintenance_start_hook(organization_id, realm_id)
        realm_vlobs = self._get_realm_vlobs(organization_id, realm_id)
        setattr(changes, attr, Task(realm_id, realm_vlobs))

    def _maintenance_finished_hook(self, attr, organization_id, realm_id):
        changes = self._per_realm_changes[(organization_id, realm_id)]

        task = getattr(changes, attr)
        assert task
        if not task.is_finished():
            return False

        realm_vlobs = task.get_vlobs()
        for vlob_id, vlob in realm_vlobs.items():
            self._vlobs[(organization_id, vlob_id)] = vlob

        setattr(changes, attr, None)
        changes.garbage_collection = None
        return True

    def _maintenance_garbage_collection_start_hook(self, organization_id, realm_id):
        self._maintenance_start_hook(
            "garbage_collection", GarbageCollection, organization_id, realm_id
        )

    def _maintenance_garbage_collection_is_finished_hook(self, organization_id, realm_id):
        return self._maintenance_finished_hook("garbage_collection", organization_id, realm_id)

    def _maintenance_reencryption_start_hook(self, organization_id, realm_id, encryption_revision):
        self._maintenance_start_hook("reencryption", Reencryption, organization_id, realm_id)

    def _maintenance_reencryption_is_finished_hook(
        self, organization_id, realm_id, encryption_revision
    ):
        return self._maintenance_finished_hook("reencryption", organization_id, realm_id)

    def _get_vlob(self, organization_id, vlob_id):
        try:
            return self._vlobs[(organization_id, vlob_id)]

        except KeyError:
            raise VlobNotFoundError(f"Vlob `{vlob_id}` doesn't exist")

    def _check_realm_read_access(self, organization_id, realm_id, user_id, encryption_revision):
        can_read_roles = (
            RealmRole.OWNER,
            RealmRole.MANAGER,
            RealmRole.CONTRIBUTOR,
            RealmRole.READER,
        )
        self._check_realm_access(
            organization_id, realm_id, user_id, encryption_revision, can_read_roles
        )

    def _check_realm_write_access(self, organization_id, realm_id, user_id, encryption_revision):
        can_write_roles = (RealmRole.OWNER, RealmRole.MANAGER, RealmRole.CONTRIBUTOR)
        self._check_realm_access(
            organization_id, realm_id, user_id, encryption_revision, can_write_roles
        )

    def _check_realm_access(
        self,
        organization_id,
        realm_id,
        user_id,
        encryption_revision,
        allowed_roles,
        expected_maintenance=False,
        check_encryption_revision=True,
    ):
        try:
            realm = self._realm_component._get_realm(organization_id, realm_id)
        except RealmNotFoundError:
            raise VlobNotFoundError(f"Realm `{realm_id}` doesn't exist")

        if realm.roles.get(user_id) not in allowed_roles:
            raise VlobAccessError()

        if expected_maintenance is False:
            if realm.status.in_maintenance:
                raise VlobInMaintenanceError(f"Realm `{realm_id}` is currently under maintenance")
        elif expected_maintenance is True:
            if not realm.status.in_maintenance:
                raise VlobNotInMaintenanceError(f"Realm `{realm_id}` not under maintenance")

        if check_encryption_revision and encryption_revision not in (
            None,
            realm.status.encryption_revision,
        ):
            raise VlobEncryptionRevisionError()

    def _check_realm_in_maintenance_access(
        self,
        organization_id,
        realm_id,
        user_id,
        encryption_revision,
        check_encryption_revision=True,
    ):
        can_do_maintenance_roles = (RealmRole.OWNER,)
        self._check_realm_access(
            organization_id,
            realm_id,
            user_id,
            encryption_revision,
            can_do_maintenance_roles,
            check_encryption_revision=check_encryption_revision,
            expected_maintenance=True,
        )

    async def _update_changes(self, organization_id, author, realm_id, src_id, src_version=1):
        changes = self._per_realm_changes[(organization_id, realm_id)]
        changes.checkpoint += 1
        changes.changes[src_id] = (author, changes.checkpoint, src_version)
        await self._send_event(
            "realm.vlobs_updated",
            organization_id=organization_id,
            author=author,
            realm_id=realm_id,
            checkpoint=changes.checkpoint,
            src_id=src_id,
            src_version=src_version,
        )

    async def create(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        vlob_id: UUID,
        timestamp: pendulum.Pendulum,
        blob: bytes,
    ) -> None:
        self._check_realm_write_access(
            organization_id, realm_id, author.user_id, encryption_revision
        )

        key = (organization_id, vlob_id)
        if key in self._vlobs:
            raise VlobAlreadyExistsError()

        self._vlobs[key] = Vlob(realm_id, [(blob, author, timestamp, None)])
        await self._update_changes(organization_id, author, realm_id, vlob_id)

    async def read(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: Optional[int] = None,
        timestamp: Optional[pendulum.Pendulum] = None,
        to_quarantine: Optional[pendulum.Pendulum] = None,
    ) -> Tuple[int, bytes, DeviceID, pendulum.Pendulum]:
        vlob = self._get_vlob(organization_id, vlob_id)

        self._check_realm_read_access(
            organization_id, vlob.realm_id, author.user_id, encryption_revision
        )
        if version is None:
            if timestamp is None:
                version = vlob.current_version
            else:
                for i in range(vlob.current_version, 0, -1):
                    if vlob.data[i - 1][2] <= timestamp:
                        version = i
                        break
                else:
                    raise VlobVersionError()
        try:
            return (version, *vlob.data[version - 1])

        except IndexError:
            raise VlobVersionError()

    async def update(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        encryption_revision: int,
        vlob_id: UUID,
        version: int,
        timestamp: pendulum.Pendulum,
        blob: bytes,
    ) -> None:
        vlob = self._get_vlob(organization_id, vlob_id)

        self._check_realm_write_access(
            organization_id, vlob.realm_id, author.user_id, encryption_revision
        )

        if version - 1 != vlob.current_version:
            raise VlobVersionError()
        if timestamp < vlob.data[vlob.current_version - 1][2]:
            raise VlobTimestampError(timestamp, vlob.data[vlob.current_version - 1][2])
        vlob.data.append((blob, author, timestamp, None))

        await self._update_changes(organization_id, author, vlob.realm_id, vlob_id, version)

    async def group_check(
        self, organization_id: OrganizationID, author: DeviceID, to_check: List[dict]
    ) -> List[dict]:
        changed = []
        for item in to_check:
            vlob_id = item["vlob_id"]
            version = item["version"]
            if version == 0:
                changed.append({"vlob_id": vlob_id, "version": version})
            else:
                try:
                    vlob = self._get_vlob(organization_id, vlob_id)
                except VlobNotFoundError:
                    continue

                try:
                    self._check_realm_read_access(
                        organization_id, vlob.realm_id, author.user_id, None
                    )
                except (VlobNotFoundError, VlobAccessError, VlobInMaintenanceError):
                    continue

                if vlob.current_version != version:
                    changed.append({"vlob_id": vlob_id, "version": vlob.current_version})

        return changed

    async def poll_changes(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID, checkpoint: int
    ) -> Tuple[int, Dict[UUID, int]]:
        self._check_realm_read_access(organization_id, realm_id, author.user_id, None)

        changes = self._per_realm_changes[(organization_id, realm_id)]
        changes_since_checkpoint = {
            src_id: src_version
            for src_id, (_, change_checkpoint, src_version) in changes.changes.items()
            if change_checkpoint > checkpoint
        }
        return (changes.checkpoint, changes_since_checkpoint)

    async def list_versions(
        self, organization_id: OrganizationID, author: DeviceID, vlob_id: UUID
    ) -> Dict[int, Tuple[pendulum.Pendulum, DeviceID]]:
        vlobs = self._get_vlob(organization_id, vlob_id)

        self._check_realm_read_access(organization_id, vlobs.realm_id, author.user_id, None)
        return {k: (v[2], v[1]) for (k, v) in enumerate(vlobs.data, 1)}

    async def maintenance_get_reencryption_batch(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        size: int,
    ) -> List[Tuple[UUID, int, bytes]]:
        self._check_realm_in_maintenance_access(
            organization_id, realm_id, author.user_id, encryption_revision
        )

        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert changes.reencryption

        return changes.reencryption.get_batch(size)

    async def maintenance_get_garbage_collection_vlobs(
        self, organization_id: OrganizationID, author: DeviceID, realm_id: UUID
    ) -> Tuple[UUID, datetime]:
        self._check_realm_in_maintenance_access(
            organization_id,
            realm_id,
            author.user_id,
            encryption_revision=None,
            check_encryption_revision=False,
        )

        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert changes.garbage_collection

        return changes.garbage_collection.get_one_vlob()

    async def maintenance_save_garbage_collection_vlob(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        vlob_id: UUID,
        versions_to_erase: List[int],
    ) -> Tuple[int, int]:
        self._check_realm_in_maintenance_access(
            organization_id,
            realm_id,
            author.user_id,
            encryption_revision=None,
            check_encryption_revision=False,
        )

        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert changes.garbage_collection

        total, done = changes.garbage_collection.save_vlob(vlob_id, versions_to_erase)
        return total, done

    async def maintenance_save_reencryption_batch(
        self,
        organization_id: OrganizationID,
        author: DeviceID,
        realm_id: UUID,
        encryption_revision: int,
        batch: List[Tuple[UUID, int, bytes]],
    ) -> Tuple[int, int]:
        self._check_realm_in_maintenance_access(
            organization_id, realm_id, author.user_id, encryption_revision
        )

        changes = self._per_realm_changes[(organization_id, realm_id)]
        assert changes.reencryption

        total, done = changes.reencryption.save_batch(batch)

        return total, done
