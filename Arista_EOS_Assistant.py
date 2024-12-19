"""Arista EOS gNMI Assistant - A Streamlit application for network device management."""

import os
import logging
import json
import time
import uuid
import asyncio
import pandas as pd
import streamlit as st
from openai import AzureOpenAI
from dotenv import load_dotenv

from gnmi_tools import (
    gnmi_configure,
    gnmi_get,
    apply_cli_commands,
    load_device_info
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Set up Streamlit page
st.set_page_config(page_title="Arista EOS gNMI Assistant", page_icon=":gear:")

# Custom CSS for sidebar styling
st.markdown("""
    <style>
    .sidebar-content {
        font-size: 0.8rem;
    }
    .scrollable-container {
        max-height: 200px;
        overflow-y: auto;
        border: 1px solid #ddd;
        padding: 10px;
        border-radius: 5px;
        background-color: #f8f9fa;
        margin-bottom: 15px;
    }
    .template-label {
        font-size: 0.9rem;
        margin-bottom: 8px;
        color: #555;
    }
    </style>
""", unsafe_allow_html=True)

def read_template(template_name: str) -> str:
    """Read a template file from the device_config_templates directory.
    
    Args:
        template_name: Name of the template file to read
        
    Returns:
        str: Content of the template file
        
    Raises:
        FileNotFoundError: If template file doesn't exist
    """
    template_path = os.path.join("device_config_templates", template_name)
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template {template_name} not found")
    
    with open(template_path, 'r') as f:
        return f.read()

# Initialize Azure OpenAI client
client = AzureOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    api_version="2024-05-01-preview"
)

# Get Assistant ID from environment
assistant_id = os.environ.get('AZURE_OPENAI_ASSISTANT_ID')

# Initialize session state
if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "thread_id" not in st.session_state:
    st.session_state.thread_id = None
if "openai_model" not in st.session_state:
    st.session_state.openai_model = "gpt-4o-mini"
if "messages" not in st.session_state:
    st.session_state.messages = []
if "tool_results" not in st.session_state:
    st.session_state.tool_results = {}
if "last_poll_run_status" not in st.session_state:
    st.session_state.last_poll_run_status = "Not started"
if "csv_content" not in st.session_state:
    st.session_state.csv_content = None

async def poll_run(client, thread_id: str, run_id: str, timeout: int = 300):
    """Poll the run status until completion or timeout."""
    start_time = time.time()
    status_placeholder = st.empty()
    
    while time.time() - start_time < timeout:
        run = client.beta.threads.runs.retrieve(
            thread_id=thread_id,
            run_id=run_id
        )
        st.session_state.current_run_status = run.status
        st.session_state.last_poll_run_status = run.status
        status_placeholder.text(f"Current status: {run.status}")
        logger.info(f"Poll Run Status: {run.status}")
        
        if 'last_poll_status' in st.session_state:
            st.session_state.last_poll_status.text(
                f"Last Poll Run: {run.status}"
            )
        
        if run.status in ['completed', 'requires_action', 'failed']:
            return run
            
        await asyncio.sleep(1)
    
    raise TimeoutError("Run polling timed out")

async def execute_tool(tool_call):
    """Execute a tool based on the assistant's request."""
    tool_name = tool_call.function.name
    arguments = json.loads(tool_call.function.arguments)
    
    try:
        if tool_name == "gnmi_configure":
            result = await gnmi_configure(arguments)
            
            if "error" in result:
                st.session_state.tool_results[tool_name] = False
                return {
                    "tool_call_id": tool_call.id,
                    "output": f"Error: {result['error']}"
                }
            
            if isinstance(arguments.get("target"), list):
                success = all('notification' in r for r in result.get("responses", []))
            else:
                success = 'notification' in result
                
            st.session_state.tool_results[tool_name] = success
            
            return {
                "tool_call_id": tool_call.id,
                "output": (
                    "Configuration successful" if success
                    else "Configuration failed"
                )
            }
        
        elif tool_name == "gnmi_get":
            result = await gnmi_get(arguments)
            
            if "error" in result:
                st.session_state.tool_results[tool_name] = False
                return {
                    "tool_call_id": tool_call.id,
                    "output": f"Error: {result['error']}"
                }
            
            st.session_state.tool_results[tool_name] = True
            
            if isinstance(arguments.get("target"), list):
                return {
                    "tool_call_id": tool_call.id,
                    "output": json.dumps(result.get("responses", []))
                }
            else:
                return {
                    "tool_call_id": tool_call.id,
                    "output": json.dumps(result.get('updates', []))
                }
            
        elif tool_name == "apply_cli_commands":
            result = await apply_cli_commands(arguments)
            
            if "error" in result:
                st.session_state.tool_results[tool_name] = False
                return {
                    "tool_call_id": tool_call.id,
                    "output": f"Error: {result['error']}"
                }
            
            success = result.get("status") == "success"
            st.session_state.tool_results[tool_name] = success
            
            return {
                "tool_call_id": tool_call.id,
                "output": result.get("confirmation", "Operation completed")
            }
            
        elif tool_name == "read_template":
            template_name = arguments.get("template_name")
            try:
                content = read_template(template_name)
                st.session_state.tool_results[tool_name] = True
                return {
                    "tool_call_id": tool_call.id,
                    "output": content
                }
            except FileNotFoundError as e:
                st.session_state.tool_results[tool_name] = False
                return {
                    "tool_call_id": tool_call.id,
                    "output": f"Error: {str(e)}"
                }
        
        else:
            raise ValueError(f"Unknown tool: {tool_name}")
            
    except Exception as e:
        st.session_state.tool_results[tool_name] = False
        return {
            "tool_call_id": tool_call.id,
            "output": f"Error: {str(e)}"
        }

# Sidebar configuration
st.sidebar.title("Arista EOS gNMI Assistant")

# Display available devices in a scrollable container
st.sidebar.subheader("Available Devices")
devices = load_device_info().get("devices", {})
devices_html = "<div class='scrollable-container sidebar-content'>"
for device_id, device in devices.items():
    devices_html += f"<div>{device_id}: {device['description']}</div>"
devices_html += "</div>"
st.sidebar.markdown(devices_html, unsafe_allow_html=True)

# Template Management Section
st.sidebar.subheader("Template Management")
st.sidebar.markdown("<div class='template-label'>Available Templates:</div>", unsafe_allow_html=True)

template_files = [f for f in os.listdir("device_config_templates") if f.endswith(".jinja2")]
templates_html = "<div class='scrollable-container sidebar-content'>"
if template_files:
    for template in template_files:
        templates_html += f"<div>{template}</div>"
else:
    templates_html += "<div>No templates available</div>"
templates_html += "</div>"
st.sidebar.markdown(templates_html, unsafe_allow_html=True)

# File Upload Section
st.sidebar.subheader("Parameter File Upload")
uploaded_file = st.sidebar.file_uploader("Upload Parameters CSV", type=['csv'])
if uploaded_file:
    try:
        # Read CSV content
        df = pd.read_csv(uploaded_file, header=None, names=['Parameter', 'Value'])
        
        # Convert DataFrame to dictionary
        parameters = dict(zip(df['Parameter'], df['Value']))
        
        # Store as JSON
        st.session_state.csv_content = json.dumps(parameters, indent=2)
        
        st.sidebar.success("File uploaded successfully!")
        st.sidebar.subheader("Parameters")
        st.sidebar.json(parameters)
    except Exception as e:
        st.sidebar.error(f"Error reading file: {str(e)}")
        st.session_state.csv_content = None

# Restart Session button
if st.sidebar.button("Restart Session"):
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.thread_id = None
    st.session_state.messages = []
    st.session_state.tool_results = {}
    st.session_state.last_poll_run_status = "Not started"
    st.session_state.csv_content = None
    st.rerun()

# Main chat interface
if not st.session_state.thread_id:
    thread = client.beta.threads.create()
    st.session_state.thread_id = thread.id
    intro_message = """Hello! I'm your Arista EOS gNMI Assistant. I can help you configure your network devices using gNMI. 
    I understand natural language and can translate your requests into the correct gNMI paths using YANG models.
    
    You can refer to devices by their hostnames (e.g., 'ceos-spine-1', 'ceos-leaf-1') or specify groups like 'all spine switches' or 'all leaf switches'.
    
    Available devices are shown in the sidebar. You can also view and read Jinja2 templates listed in the sidebar.
    
    To view a template, just ask me to show you any template listed in the sidebar.
    
    You can upload a parameters CSV file using the file uploader in the sidebar, and I'll help you apply those parameters to the templates.
    """
    st.session_state.messages.append({"role": "assistant", "content": intro_message})

# Display chat history
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# Handle user input
if prompt := st.chat_input("Enter your message"):
    # If CSV content exists, append it to the prompt
    if st.session_state.csv_content:
        full_prompt = f"{prompt}\n\nHere are the parameters from the uploaded CSV file:\n{st.session_state.csv_content}"
    else:
        full_prompt = prompt

    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Create message in thread
    client.beta.threads.messages.create(
        thread_id=st.session_state.thread_id,
        role="user",
        content=full_prompt
    )

    # Create and poll run
    run = client.beta.threads.runs.create(
        thread_id=st.session_state.thread_id,
        assistant_id=assistant_id,
        model=st.session_state.openai_model
    )
    
    run = asyncio.run(poll_run(client, st.session_state.thread_id, run.id))

    # Handle run status
    while run.status == 'requires_action':
        tool_calls = run.required_action.submit_tool_outputs.tool_calls
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        tasks = [execute_tool(tool_call) for tool_call in tool_calls]
        tool_outputs = loop.run_until_complete(asyncio.gather(*tasks))

        # Submit tool outputs and poll again
        run = client.beta.threads.runs.submit_tool_outputs(
            thread_id=st.session_state.thread_id,
            run_id=run.id,
            tool_outputs=tool_outputs
        )
        run = loop.run_until_complete(
            poll_run(client, st.session_state.thread_id, run.id)
        )

    if run.status == 'completed':
        messages = client.beta.threads.messages.list(
            thread_id=st.session_state.thread_id
        )

        # Process and display assistant messages
        assistant_messages = [
            message for message in messages 
            if message.run_id == run.id and message.role == "assistant"
        ]
        for message in assistant_messages:
            content = message.content[0].text.value
            st.session_state.messages.append({
                "role": "assistant",
                "content": content
            })
            with st.chat_message("assistant"):
                st.markdown(content)
    else:
        logger.error(f"Run ended with unexpected status: {run.status}")
        st.error(f"An error occurred: {run.status}")

    # Update sidebar status
    if 'last_poll_status' in st.session_state:
        st.session_state.last_poll_status.text(
            f"Last Poll Run: {st.session_state.last_poll_run_status}"
        )

    # Force UI update
    st.rerun()
