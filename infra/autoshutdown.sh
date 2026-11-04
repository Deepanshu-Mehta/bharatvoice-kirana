#!/usr/bin/env bash
# Run on the GPU box in the background: powers off after IDLE_MIN minutes with <5% GPU utilisation.
# Usage: nohup bash autoshutdown.sh 20 &
IDLE_MIN="${1:-20}"; idle=0
while true; do
  util=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits | sort -nr | head -1)
  if [ "${util:-0}" -lt 5 ]; then idle=$((idle+1)); else idle=0; fi
  if [ "$idle" -ge "$IDLE_MIN" ]; then echo "idle ${IDLE_MIN} min, shutting down"; sudo shutdown -h now; fi
  sleep 60
done
