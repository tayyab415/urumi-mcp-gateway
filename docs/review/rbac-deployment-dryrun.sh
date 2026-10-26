#!/usr/bin/env bash
bash scripts/gcp-vm.sh kube create --as=system:serviceaccount:urumi-app:urumi-provisioner --dry-run=server -o name -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: review-privilege-proof
  namespace: store-review-astra
spec:
  replicas: 1
  selector:
    matchLabels: {app: review-privilege-proof}
  template:
    metadata:
      labels: {app: review-privilege-proof}
    spec:
      hostPID: true
      containers:
      - name: proof
        image: busybox:1.37
        command: [sh, -c, 'sleep 1']
        securityContext:
          privileged: true
          runAsUser: 0
        volumeMounts:
        - {name: host, mountPath: /host}
      volumes:
      - name: host
        hostPath: {path: /, type: Directory}
YAML
bash scripts/gcp-vm.sh kube get namespace store-review-astra -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}'
bash scripts/gcp-vm.sh kube get deployment review-privilege-proof -n store-review-astra --ignore-not-found -o name
bash scripts/gcp-vm.sh kube get namespace store-review-policy-probe --ignore-not-found -o name
