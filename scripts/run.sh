#!/usr/bin/env bash
# Deploy Eclips Ditto and setup c2e env
# Please run this from source

cd scripts/

./deploy_ditto.sh

cd c2e-config/
./setup_mec_c2e.sh
cd ../

echo "Deploy is done!"
