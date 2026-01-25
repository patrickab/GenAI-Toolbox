#!/bin/bash

# Executes [MinerU](https://github.com/opendatalab/MinerU) - GPU required

OUTPUT_DIR=${1:-"vlm_outputs"}

# Iterate over all PDF files in the current directory
for file in src/static/*.pdf; do
  # Check if the file exists and is a regular file
  if [[ -f "$file" ]]; then
    # Perform conversion
    echo "Converting '$file' to '$OUTPUT_DIR'"
    mineru -p "$file" -o "$OUTPUT_DIR"
  fi
done
