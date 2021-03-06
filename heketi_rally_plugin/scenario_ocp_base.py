import json
import time

from kubernetes import client as k_client
from rally.task import atomic
from rally.task import scenario

from heketi_rally_plugin import utils


class OCPScenarioBase(scenario.Scenario):
    """Base class for OCP scenarios."""

    @property
    def client(self):
        """Shortcut for reaching out an OCP client."""
        return self.context["ocp_client"]

    # --- PV ---

    @atomic.action_timer("pv_list")
    def _pv_list(self):
        """Atomic action for listing Persistent Volumes in OCP."""
        return self.client.list_persistent_volume()

    @atomic.action_timer("pv_get")
    def _pv_get(self, name):
        return self.client.read_persistent_volume(name=name)

    # --- PVC ---

    @atomic.action_timer("pvc_create")
    def _pvc_create(self, storage_class,
                    namespace='default', size=1, name_prefix="rally",
                    creation_timeout=120.0, creation_waiting_step=1.7,
                    delete_pvc_if_failed=True):
        """Atomic action for creating Persistent Volume Claim in OCP."""
        if name_prefix and name_prefix[-1] != '-':
            name_prefix += "-"
        name_prefix.replace('_', '-')
        name = "%s%s" % (name_prefix, utils.get_random_str(14))

        pvc_body = k_client.V1PersistentVolumeClaim(
            api_version="v1",
            kind="PersistentVolumeClaim",
            metadata={
                "name": name,
                "annotations": {
                    "volume.beta.kubernetes.io/storage-class": storage_class,
                },
            },
            spec={
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "%sGi" % size}},
            },
        )
        self.client.create_namespaced_persistent_volume_claim(
            namespace=namespace, body=pvc_body)

        # Wait for PVC to be bound to a PV
        time.sleep(creation_waiting_step)
        pvc = self._pvc_get(name=name, namespace=namespace)
        start_time = time.time()
        while (pvc.status.phase.lower() != 'bound' and
               time.time() - start_time < creation_timeout):
            time.sleep(creation_waiting_step)
            pvc = self._pvc_get(name=name, namespace=namespace)
        if pvc.status.phase.lower() != 'bound':
            if delete_pvc_if_failed:
                self._pvc_delete(name, namespace)
            raise Exception(
                'Failed to wait for PVC to reach Bound status. '
                'PVC name is "%s" and its status is "%s"' % (
                    name, pvc.status.phase)
            )
        return pvc

    @atomic.action_timer("pvc_get")
    def _pvc_get(self, name, namespace='default'):
        return self.client.read_namespaced_persistent_volume_claim(
            name=name, namespace=namespace)

    @atomic.action_timer("pvc_list")
    def _pvc_list(self, namespace=None):
        """Atomic action for listing Persistent Volumes Claims in OCP."""
        if namespace:
            return self.client.list_namespaced_persistent_volume_claim(
                namespace)
        return self.client.list_persistent_volume_claim_for_all_namespaces()

    @atomic.action_timer("pvc_delete")
    def _pvc_delete(self, name, namespace='default',
                    deletion_timeout=120.0, deletion_waiting_step=1.4):
        self.client.delete_namespaced_persistent_volume_claim(
            name=name, namespace=namespace, body=k_client.V1DeleteOptions())

        # Wait for PV to be absent
        start_time = time.time()
        while time.time() - start_time < deletion_timeout:
            try:
                self._pv_get(name=name)
            except k_client.rest.ApiException as e:
                if int(json.loads(e.body)["code"]) == 404:
                    return
                raise
            time.sleep(deletion_waiting_step)
        raise Exception("Failed to wait for '%s' PV to be deleted." % name)
