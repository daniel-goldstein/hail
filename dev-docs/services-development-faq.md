# FAQ when developing Hail Batch

#### I messed up the Batch database in my dev namespace. How do I start fresh?

If you only want to delete the Batch database and leave the other databases alone,
you can submit a dev deploy using the `delete_batch_tables` job. The following
will create a dev deploy that removes your current Batch database and redeploys
Batch with a fresh one:

```bash
hailctl dev deploy -b <github_username>/hail:<your branch> -s delete_batch_tables,deploy_batch
```

If you want to start totally clean, another option is to delete your dev namespace's
database completely by deleting the Kubernetes resources that compromise the dev
database.
To start with an entirely clean slate, across all services, we want
to delete the `StatefulSet` *and* the underlying data. The underlying data belongs
to a `PersistentVolumeClaim`, so to delete everything perform the following:

```bash
kubectl -n <my_namespace> delete statefulset db
# When that's done...
kubectl -n <my_namespace> delete pvc mysql-persistent-storage-db-0
```
