#!/usr/bin/env python3
import asyncio
import httpx

async def check_server_status():
    """Check which ports have servers running and what they respond with"""
    
    ports_to_check = [4000, 5001, 6000]
    
    for port in ports_to_check:
        print(f"\n=== Checking port {port} ===")
        
        # Check basic connectivity
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"http://localhost:{port}/")
                print(f"✅ Port {port} is responding: {response.status_code}")
        except Exception as e:
            print(f"❌ Port {port} not responding: {e}")
            continue
            
        # Check for OAuth metadata
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"http://localhost:{port}/.well-known/oauth-authorization-server")
                if response.status_code == 200:
                    print(f"✅ Port {port} has OAuth authorization server metadata")
                else:
                    print(f"❌ Port {port} OAuth auth server metadata: {response.status_code}")
        except Exception as e:
            print(f"❌ Port {port} OAuth auth server metadata error: {e}")
            
        # Check for MCP resource metadata
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"http://localhost:{port}/.well-known/oauth-protected-resource")
                if response.status_code == 200:
                    data = response.json()
                    print(f"✅ Port {port} has MCP resource metadata: {data}")
                else:
                    print(f"❌ Port {port} MCP resource metadata: {response.status_code}")
        except Exception as e:
            print(f"❌ Port {port} MCP resource metadata error: {e}")
            
        # Check if it's a FastMCP server by looking for the MCP endpoint
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Try to send a simple HTTP request that might reveal MCP capabilities
                response = await client.get(f"http://localhost:{port}/docs")
                if response.status_code == 200:
                    print(f"✅ Port {port} has FastAPI docs (likely FastMCP)")
                else:
                    print(f"❌ Port {port} FastAPI docs: {response.status_code}")
        except Exception as e:
            print(f"❌ Port {port} FastAPI docs error: {e}")

if __name__ == "__main__":
    asyncio.run(check_server_status())