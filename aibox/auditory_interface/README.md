# Auditory Interface

The auditory_interface provides a functionality to interact with HANS (Automated Hand Navigation System) through continuous auditory communication. Auditory inputs provided by the user are transcribed, and based on the detected intent and available tools selected LLM (Large Language Model) interacts with HANS to modify its behaviour, or extract some information about its current state.

# Core Functionalities

## Sppech-to-Text (SST): Enables listening for the wake-word, and transcribing the following prompt.

## MCP integration: Enables sending and receiving the signals from and to HANS via MCP tools.

## Auditory control of the grasping process: Enables running the auditory communication protocol in parallel with HANS.

# Folder Structure

auditory_interface/
├── audio_engine.py
├── main.py
├── mcp_config.py
├── query_processing.py
├── server_hans.py
└── resources/...

# Brief File Descriptions

## audio_engine.py

Defines functions related to the processing of auditory inputs.

## main.py

Runs the whole system (MCP client interacting with MCP server + HANS).

## mcp_config.py

Basic configuration scripts.

## query_processing.py

Defines logic related to processing of user queries.

## server_hans.py

Defines MCP server, that is able to interact with HANS.

# Usage

After installation of the required packages, run main.py script with auditory_interface as the working directory.