"""
Customer Management Module for Content Curator
Handles customer creation, modification, and deletion with multi-step workflows
Modeled after interest_mgmt.py with CSV-based data persistence
"""

import pandas as pd
import json
import os
import re
import time
import shutil
import requests
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from bs4 import BeautifulSoup

# Import existing components for integration
try:
    from entity_resolver import calculate_similarity
    HAS_ENTITY_RESOLVER = True
except ImportError:
    HAS_ENTITY_RESOLVER = False

try:
    from cache_mgmt import CustomerCacheManager
    HAS_CACHE_SUPPORT = True
except ImportError:
    HAS_CACHE_SUPPORT = False

try:
    from customer_insights import CustomerInsightsAgent
    HAS_CUSTOMER_INSIGHTS = True
except ImportError:
    HAS_CUSTOMER_INSIGHTS = False

try:
    from customer_insights import CustomerDataManager
    HAS_CUSTOMER_DATA_MANAGER = True
except ImportError:
    HAS_CUSTOMER_DATA_MANAGER = False


class CustomerManager:
    """Manages customer data with multi-step workflow processing"""
    
    def __init__(self, data_directory: str = "data", cache_directory: str = "cache"):
        self.data_directory = data_directory
        self.cache_directory = cache_directory
        self.customers_file = os.path.join(data_directory, "customers.csv")
        
        # Ensure directories exist
        os.makedirs(data_directory, exist_ok=True)
        os.makedirs(cache_directory, exist_ok=True)
        
        # Session state for multi-step workflows (like interest_mgmt.py)
        self.pending_confirmations = {}  # session_id -> action_data
        self.confirmation_timeouts = {}  # session_id -> timestamp
        
        # Initialize cache manager if available
        if HAS_CACHE_SUPPORT:
            self.cache_manager = CustomerCacheManager(data_directory, cache_directory)
        else:
            self.cache_manager = None
            
        # Initialize customer insights if available
        if HAS_CUSTOMER_INSIGHTS:
            self.insights_agent = CustomerInsightsAgent(data_directory)
        else:
            self.insights_agent = None

        if HAS_CUSTOMER_DATA_MANAGER:
            self.customer_data_manager = CustomerDataManager(data_directory)
        else:
            self.customer_data_manager = None

    def is_customer_command(self, message: str) -> bool:
        """Check if message is a customer management command"""
        message_lower = message.lower().strip()
        
        # Keywords that indicate customer management
        customer_keywords = [
            'customer', 'customers', 'client', 'clients', 'cluster', 'clusters',
            'add', 'remove', 'delete', 'list', 'show', 'create', 'new',
            'update', 'change', 'move', 'my customers', 'company', 'companies'
        ]
        
        # Check for any customer-related keywords
        for keyword in customer_keywords:
            if keyword in message_lower:
                return True
                
        return False

    def process_command_with_session(self, command: str, session_id: str) -> Dict:
        """
        Process command with session-based workflow tracking
        Returns dict with success, action, response, and any additional data
        """
        try:
            # Clean up expired sessions first
            self.cleanup_expired_sessions()
            
            # Check if this is a continuation of an existing workflow
            if session_id in self.pending_confirmations:
                return self._handle_workflow_continuation(command, session_id)
            
            # Process new command
            return self._process_new_command(command, session_id)
            
        except Exception as e:
            return {
                'success': False,
                'action': 'error',
                'response': f"❌ Error processing customer command: {str(e)}",
                'error': str(e)
            }

    def _process_new_command(self, command: str, session_id: str) -> Dict:
        """Process a new customer management command"""
        command_lower = command.lower().strip()
        
        # List customers/clusters
        if any(phrase in command_lower for phrase in ['list', 'show']) and any(phrase in command_lower for phrase in ['customer', 'client']):
            return self._process_list_command()
            
        # Add/create customer
        elif any(phrase in command_lower for phrase in ['add', 'create', 'new']) and any(phrase in command_lower for phrase in ['customer', 'client']):
            return self._start_add_workflow(session_id)
            
        # Remove/delete customer or cluster
        elif any(phrase in command_lower for phrase in ['remove', 'delete']) and any(phrase in command_lower for phrase in ['customer', 'client', 'cluster']):
            return self._start_remove_workflow(session_id)
            
        # Update/change/move customer
        elif any(phrase in command_lower for phrase in ['update', 'change', 'move']) and any(phrase in command_lower for phrase in ['customer', 'client']):
            return self._start_update_workflow(session_id)
            
        # Help
        elif 'help' in command_lower:
            return self._get_customer_help()

        # Exit Management Mode
        elif command_lower in ['exit', 'quit', 'done', 'stop', 'cancel']:
            return {
                'success': True,
                'action': 'workflow_cancelled',
                'response': '✅ Customer management complete. You can now ask about customers or start other operations.',
                'exit_mode': True
            }
            
        else:
            return {
                'success': False,
                'action': 'not_customer_command',
                'exit_mode': True  # Signal to exit customer management mode
            }

    def _process_list_command(self) -> Dict:
        """List all customers organized by cluster - use CustomerDataManager for proper sorting"""
        try:
            # Use CustomerDataManager for proper natural sorting
            if HAS_CUSTOMER_DATA_MANAGER and self.customer_data_manager:
                clusters = self.customer_data_manager.get_clusters()  # Already sorted correctly
                all_customers = self.customer_data_manager.get_all_customers()

                if not clusters and not all_customers:
                    return {
                        'success': True,
                        'action': 'list',
                        'response': "📋 No customers found. Use 'add customer' to create your first customer record.",
                        'customers': []
                    }

                response_parts = [
                    "📋 **Your Customers by Cluster:**",
                    ""
                ]

                total_customers = 0
                cluster_data = []

                for cluster in clusters:  # clusters already sorted with natural_sort_key
                    cluster_customers = self.customer_data_manager.get_customers_by_cluster(cluster)
                    customer_count = len(cluster_customers)
                    total_customers += customer_count

                    response_parts.append(f"**{cluster}** ({customer_count} customers):")

                    customers_in_cluster = []
                    for customer in cluster_customers:
                        customer_name = customer.get('customer_name', 'Unknown')
                        response_parts.append(f"  • {customer_name}")
                        customers_in_cluster.append(customer_name)

                    response_parts.append("")

                    cluster_data.append({
                        'cluster': cluster,
                        'count': customer_count,
                        'customers': customers_in_cluster
                    })

                response_parts.extend([
                    f"**Total: {total_customers} customers across {len(cluster_data)} clusters**",
                    "",
                    "*Commands: 'add customer', 'remove customer', 'update customer', or 'exit' when done*"
                ])

                return {
                    'success': True,
                    'action': 'list',
                    'response': "\n".join(response_parts),
                    'customers': cluster_data,
                    'total_customers': total_customers
                }
            else:
                # Simple error if customer_data_manager not available
                return {
                    'success': False,
                    'action': 'error',
                    'response': "❌ Customer data manager not available",
                    'error': "Customer data manager not available"
                }

        except Exception as e:
            return {
                'success': False,
                'action': 'error',
                'response': f"❌ Error listing customers: {str(e)}",
                'error': str(e)
            }

    def _start_add_workflow(self, session_id: str) -> Dict:
        """Start the add customer workflow"""
        # Store workflow state
        self.pending_confirmations[session_id] = {
            'workflow': 'add_customer',
            'step': 'cluster_name',
            'data': {},
            'timestamp': time.time()
        }
        
        # Show existing clusters for reference
        clusters_info = self._get_cluster_summary()
        
        response = "📋 **Add New Customer**\n\n"
        if clusters_info:
            response += f"**Existing clusters:** {clusters_info}\n\n"
        
        response += "**Step 1 of 4:** What cluster should this customer belong to?\n"
        response += "*(You can use an existing cluster or create a new one)*"
        
        return {
            'success': True,
            'action': 'add_workflow_started',
            'response': response,
            'workflow_step': 'cluster_name'
        }

    def _start_remove_workflow(self, session_id: str) -> Dict:
        """Start the remove customer/cluster workflow"""
        customers_df = self._load_customers_csv()
        if customers_df is None or len(customers_df) == 0:
            return {
                'success': False,
                'action': 'error',
                'response': "❌ No customers found to remove."
            }
        
        # Store workflow state
        self.pending_confirmations[session_id] = {
            'workflow': 'remove',
            'step': 'select_target',
            'data': {},
            'timestamp': time.time()
        }
        
        # Show options
        clusters = customers_df['cluster'].unique().tolist()
        response = "🗑️ **Remove Customer or Cluster**\n\n"
        response += "**What would you like to remove?**\n"
        response += "• Enter a **customer name** to remove a specific customer\n"
        response += "• Enter a **cluster name** to remove all customers in that cluster\n\n"
        response += f"**Available clusters:** {', '.join(clusters)}\n\n"
        response += "*⚠️ Warning: Removal cannot be undone and will delete all cached data*"
        
        return {
            'success': True,
            'action': 'remove_workflow_started',
            'response': response,
            'workflow_step': 'select_target'
        }

    def _start_update_workflow(self, session_id: str) -> Dict:
        """Start the update customer workflow"""
        customers_df = self._load_customers_csv()
        if customers_df is None or len(customers_df) == 0:
            return {
                'success': False,
                'action': 'error',
                'response': "❌ No customers found to update."
            }
        
        # Store workflow state
        self.pending_confirmations[session_id] = {
            'workflow': 'update',
            'step': 'select_customer',
            'data': {},
            'timestamp': time.time()
        }
        
        response = "✏️ **Update Customer**\n\n"
        response += "**Step 1:** Which customer would you like to update?\n"
        response += "*(Enter the customer name)*"
        
        return {
            'success': True,
            'action': 'update_workflow_started',
            'response': response,
            'workflow_step': 'select_customer'
        }

    def _handle_workflow_continuation(self, command: str, session_id: str) -> Dict:
        """Handle continuation of an existing workflow"""
        workflow_data = self.pending_confirmations[session_id]
        workflow_type = workflow_data['workflow']
        current_step = workflow_data['step']
        
        # Check for workflow exit commands
        if command.lower().strip() in ['cancel', 'exit', 'quit', 'done', 'stop']:
            del self.pending_confirmations[session_id]
            return {
                'success': True,
                'action': 'workflow_cancelled',
                'response': "✅ Customer management workflow cancelled."
            }
        
        # Route to appropriate workflow handler
        if workflow_type == 'add_customer':
            return self._handle_add_workflow(command, session_id, current_step, workflow_data)
        elif workflow_type == 'remove':
            return self._handle_remove_workflow(command, session_id, current_step, workflow_data)
        elif workflow_type == 'update':
            return self._handle_update_workflow(command, session_id, current_step, workflow_data)
        else:
            # Unknown workflow, clean up
            del self.pending_confirmations[session_id]
            return {
                'success': False,
                'action': 'error',
                'response': "❌ Unknown workflow state. Please start over."
            }

    def _handle_add_workflow(self, command: str, session_id: str, current_step: str, workflow_data: Dict) -> Dict:
        """Handle the add customer workflow steps"""
        command = command.strip()
        
        if current_step == 'cluster_name':
            # Store cluster name and ask for customer name
            workflow_data['data']['cluster'] = command
            workflow_data['step'] = 'customer_name'
            self.pending_confirmations[session_id] = workflow_data
            
            return {
                'success': True,
                'action': 'add_workflow_continue',
                'response': f"**Step 2 of 4:** What is the customer name?\n*(Cluster: {command})*",
                'workflow_step': 'customer_name'
            }
            
        elif current_step == 'customer_name':
            # Store customer name and ask for URL
            workflow_data['data']['customer_name'] = command
            workflow_data['step'] = 'reference_url'
            self.pending_confirmations[session_id] = workflow_data
            
            return {
                'success': True,
                'action': 'add_workflow_continue',
                'response': f"**Step 3 of 4:** What is the starting material URL?\n*(Customer: {command})*\n\n*Enter a URL to the company's annual report, website, or other reference material*",
                'workflow_step': 'reference_url'
            }
            
        elif current_step == 'reference_url':
            # Store URL and verify all information
            workflow_data['data']['reference_url'] = command
            workflow_data['step'] = 'confirm'
            self.pending_confirmations[session_id] = workflow_data
            
            # Check for duplicates
            duplicate_check = self._check_for_duplicates(
                workflow_data['data']['customer_name'],
                workflow_data['data']['cluster']
            )
            
            if duplicate_check['has_duplicates']:
                # Show duplicates and ask for confirmation
                response = "⚠️ **Potential Duplicate Found!**\n\n"
                response += duplicate_check['message']
                response += "\n\n**Proceed anyway?** (yes/no)"
                
                return {
                    'success': True,
                    'action': 'duplicate_warning',
                    'response': response,
                    'workflow_step': 'confirm_with_duplicates',
                    'duplicates': duplicate_check['duplicates']
                }
            else:
                # No duplicates, show confirmation
                return self._show_add_confirmation(workflow_data['data'])
                
        elif current_step == 'confirm' or current_step == 'confirm_with_duplicates':
            if command.lower() in ['yes', 'y', 'confirm', 'proceed']:
                # Execute the add operation
                result = self._execute_add_customer(workflow_data['data'], session_id)
                del self.pending_confirmations[session_id]
                return result
            else:
                # Cancel the operation
                del self.pending_confirmations[session_id]
                return {
                    'success': True,
                    'action': 'add_cancelled',
                    'response': "✅ Add customer operation cancelled."
                }
        
        # Unknown step
        del self.pending_confirmations[session_id]
        return {
            'success': False,
            'action': 'error',
            'response': "❌ Unknown workflow step. Please start over."
        }

    def _handle_remove_workflow(self, command: str, session_id: str, current_step: str, workflow_data: Dict) -> Dict:
        """Handle the remove customer/cluster workflow steps"""
        command = command.strip()
        
        if current_step == 'select_target':
            # Determine if it's a customer or cluster
            customers_df = self._load_customers_csv()
            target_info = self._identify_remove_target(command, customers_df)
            
            if not target_info['found']:
                return {
                    'success': False,
                    'action': 'target_not_found',
                    'response': f"❌ Could not find customer or cluster matching '{command}'. Please try again with an exact name."
                }
            
            # Store target info and ask for confirmation
            workflow_data['data']['target_info'] = target_info
            workflow_data['step'] = 'confirm_remove'
            self.pending_confirmations[session_id] = workflow_data
            
            # Show what will be removed
            if target_info['type'] == 'customer':
                response = f"🗑️ **Confirm Removal**\n\n"
                response += f"**Customer:** {target_info['customer_name']}\n"
                response += f"**Cluster:** {target_info['cluster']}\n"
                response += f"**Reference:** {target_info.get('reference', 'N/A')}\n\n"
                response += "⚠️ **Warning:** This will permanently delete the customer record and all cached data.\n\n"
                response += "**Are you sure?** (yes/no)"
            else:  # cluster
                response = f"🗑️ **Confirm Cluster Removal**\n\n"
                response += f"**Cluster:** {target_info['cluster']}\n"
                response += f"**Customers to remove:** {len(target_info['customers'])}\n"
                for customer in target_info['customers']:
                    response += f"  • {customer}\n"
                response += "\n⚠️ **Warning:** This will permanently delete ALL customers in this cluster and their cached data.\n\n"
                response += "**Are you sure?** (yes/no)"
            
            return {
                'success': True,
                'action': 'remove_confirmation',
                'response': response,
                'workflow_step': 'confirm_remove'
            }
            
        elif current_step == 'confirm_remove':
            if command.lower() in ['yes', 'y', 'confirm', 'delete']:
                # Execute the remove operation
                result = self._execute_remove_operation(workflow_data['data']['target_info'], session_id)
                del self.pending_confirmations[session_id]
                return result
            else:
                # Cancel the operation
                del self.pending_confirmations[session_id]
                return {
                    'success': True,
                    'action': 'remove_cancelled',
                    'response': "✅ Remove operation cancelled. No changes made."
                }
        
        # Unknown step
        del self.pending_confirmations[session_id]
        return {
            'success': False,
            'action': 'error',
            'response': "❌ Unknown workflow step. Please start over."
        }

    def _handle_update_workflow(self, command: str, session_id: str, current_step: str, workflow_data: Dict) -> Dict:
        """Handle the update customer workflow steps"""
        command = command.strip()
        
        if current_step == 'select_customer':
            # Find the customer
            customers_df = self._load_customers_csv()
            customer_info = self._find_customer_by_name(command, customers_df)
            
            if not customer_info:
                return {
                    'success': False,
                    'action': 'customer_not_found',
                    'response': f"❌ Could not find customer '{command}'. Please try again with an exact name."
                }
            
            # Store customer info and ask what to update
            workflow_data['data']['customer_info'] = customer_info
            workflow_data['step'] = 'select_update_type'
            self.pending_confirmations[session_id] = workflow_data
            
            response = f"✏️ **Update Customer: {customer_info['customer_name']}**\n\n"
            response += f"**Current cluster:** {customer_info['cluster']}\n"
            response += f"**Current reference:** {customer_info.get('reference 1', 'N/A')}\n\n"
            response += "**What would you like to update?**\n"
            response += "• Type 'cluster' to change the cluster\n"
            response += "• Type 'reference' to update the reference URL"
            
            return {
                'success': True,
                'action': 'update_type_selection',
                'response': response,
                'workflow_step': 'select_update_type'
            }
            
        elif current_step == 'select_update_type':
            if command.lower() == 'cluster':
                workflow_data['data']['update_type'] = 'cluster'
                workflow_data['step'] = 'new_cluster'
                self.pending_confirmations[session_id] = workflow_data
                
                clusters_info = self._get_cluster_summary()
                response = f"**New cluster name for {workflow_data['data']['customer_info']['customer_name']}:**\n"
                if clusters_info:
                    response += f"*(Existing clusters: {clusters_info})*"
                
                return {
                    'success': True,
                    'action': 'update_cluster_input',
                    'response': response,
                    'workflow_step': 'new_cluster'
                }
                
            elif command.lower() == 'reference':
                workflow_data['data']['update_type'] = 'reference'
                workflow_data['step'] = 'new_reference'
                self.pending_confirmations[session_id] = workflow_data
                
                return {
                    'success': True,
                    'action': 'update_reference_input',
                    'response': f"**New reference URL for {workflow_data['data']['customer_info']['customer_name']}:**",
                    'workflow_step': 'new_reference'
                }
            else:
                return {
                    'success': False,
                    'action': 'invalid_update_type',
                    'response': "❌ Please type 'cluster' or 'reference'"
                }
                
        elif current_step == 'new_cluster':
            workflow_data['data']['new_value'] = command
            workflow_data['step'] = 'confirm_update'
            self.pending_confirmations[session_id] = workflow_data
            
            customer_name = workflow_data['data']['customer_info']['customer_name']
            old_cluster = workflow_data['data']['customer_info']['cluster']
            
            response = f"✏️ **Confirm Update**\n\n"
            response += f"**Customer:** {customer_name}\n"
            response += f"**Change cluster from:** {old_cluster}\n"
            response += f"**Change cluster to:** {command}\n\n"
            response += "**Confirm this change?** (yes/no)"
            
            return {
                'success': True,
                'action': 'update_confirmation',
                'response': response,
                'workflow_step': 'confirm_update'
            }
            
        elif current_step == 'new_reference':
            workflow_data['data']['new_value'] = command
            workflow_data['step'] = 'cache_handling'
            self.pending_confirmations[session_id] = workflow_data
            
            response = f"**Cache Management**\n\n"
            response += f"Do you want to keep the existing cached data or replace it with content from the new URL?\n\n"
            response += "• Type 'keep' to keep existing cached data\n"
            response += "• Type 'replace' to delete old cache and download new content"
            
            return {
                'success': True,
                'action': 'cache_choice',
                'response': response,
                'workflow_step': 'cache_handling'
            }
            
        elif current_step == 'cache_handling':
            if command.lower() in ['keep', 'replace']:
                workflow_data['data']['cache_action'] = command.lower()
                workflow_data['step'] = 'confirm_update'
                self.pending_confirmations[session_id] = workflow_data
                
                customer_name = workflow_data['data']['customer_info']['customer_name']
                old_reference = workflow_data['data']['customer_info'].get('reference 1', 'N/A')
                new_reference = workflow_data['data']['new_value']
                cache_action = workflow_data['data']['cache_action']
                
                response = f"✏️ **Confirm Update**\n\n"
                response += f"**Customer:** {customer_name}\n"
                response += f"**Old reference:** {old_reference}\n"
                response += f"**New reference:** {new_reference}\n"
                response += f"**Cache action:** {cache_action} existing data\n\n"
                response += "**Confirm this change?** (yes/no)"
                
                return {
                    'success': True,
                    'action': 'update_confirmation',
                    'response': response,
                    'workflow_step': 'confirm_update'
                }
            else:
                return {
                    'success': False,
                    'action': 'invalid_cache_choice',
                    'response': "❌ Please type 'keep' or 'replace'"
                }
                
        elif current_step == 'confirm_update':
            if command.lower() in ['yes', 'y', 'confirm']:
                # Execute the update operation
                result = self._execute_update_operation(workflow_data['data'], session_id)
                del self.pending_confirmations[session_id]
                return result
            else:
                # Cancel the operation
                del self.pending_confirmations[session_id]
                return {
                    'success': True,
                    'action': 'update_cancelled',
                    'response': "✅ Update operation cancelled. No changes made."
                }
        
        # Unknown step
        del self.pending_confirmations[session_id]
        return {
            'success': False,
            'action': 'error',
            'response': "❌ Unknown workflow step. Please start over."
        }

    # =============================================================================
    # UTILITY METHODS
    # =============================================================================

    def _load_customers_csv(self) -> Optional[pd.DataFrame]:
        """Load customers CSV file with error handling"""
        try:
            if os.path.exists(self.customers_file):
                df = pd.read_csv(self.customers_file)
                df.columns = df.columns.str.strip()
                return df
            else:
                # Create empty CSV with proper headers
                df = pd.DataFrame(columns=['cluster', 'customer_name', 'reference 1', 'cache_path'])
                df.to_csv(self.customers_file, index=False)
                return df
        except Exception as e:
            print(f"❌ Error loading customers.csv: {e}")
            return None

    def _check_for_duplicates(self, customer_name: str, cluster: str) -> Dict:
        """Check for potential duplicate customers using fuzzy matching"""
        customers_df = self._load_customers_csv()
        if customers_df is None or len(customers_df) == 0:
            return {'has_duplicates': False, 'duplicates': [], 'message': ''}
        
        duplicates = []
        customer_name_lower = customer_name.lower().strip()
        
        # Exact name match
        exact_matches = customers_df[customers_df['customer_name'].str.lower().str.strip() == customer_name_lower]
        if len(exact_matches) > 0:
            for _, match in exact_matches.iterrows():
                duplicates.append({
                    'customer_name': match['customer_name'],
                    'cluster': match['cluster'],
                    'match_type': 'exact_name'
                })
        
        # Fuzzy name matching if entity resolver is available
        if HAS_ENTITY_RESOLVER:
            for _, existing_customer in customers_df.iterrows():
                existing_name = existing_customer['customer_name']
                similarity = calculate_similarity(customer_name, existing_name)
                if similarity > 0.8 and existing_name.lower() != customer_name_lower:
                    duplicates.append({
                        'customer_name': existing_name,
                        'cluster': existing_customer['cluster'],
                        'match_type': 'fuzzy_name',
                        'similarity': similarity
                    })
        
        if duplicates:
            message = "Found potential duplicates:\n"
            for dup in duplicates:
                message += f"  • **{dup['customer_name']}** (Cluster: {dup['cluster']}) - {dup['match_type']}\n"
            
            return {
                'has_duplicates': True,
                'duplicates': duplicates,
                'message': message
            }
        
        return {'has_duplicates': False, 'duplicates': [], 'message': ''}

    def _get_cluster_summary(self) -> str:
        """Get a summary of existing clusters"""
        customers_df = self._load_customers_csv()
        if customers_df is None or len(customers_df) == 0:
            return ""
        
        clusters = customers_df['cluster'].unique()
        return ", ".join(clusters)

    def _sanitize_customer_name(self, customer_name: str) -> str:
        """Create a safe cache directory name from customer name"""
        sanitized = customer_name.lower()
        sanitized = re.sub(r'[^\w\s-]', '', sanitized)  # Remove special chars except spaces and hyphens
        sanitized = re.sub(r'[-\s]+', '_', sanitized)   # Replace spaces and hyphens with underscores
        return sanitized.strip('_')

    def _show_add_confirmation(self, data: Dict) -> Dict:
        """Show confirmation for add customer operation"""
        response = "✅ **Confirm New Customer**\n\n"
        response += f"**Cluster:** {data['cluster']}\n"
        response += f"**Customer:** {data['customer_name']}\n"
        response += f"**Reference URL:** {data['reference_url']}\n"
        response += f"**Cache directory:** {self._sanitize_customer_name(data['customer_name'])}\n\n"
        response += "**Step 4 of 4:** Confirm to add this customer? (yes/no)"
        
        return {
            'success': True,
            'action': 'add_confirmation',
            'response': response,
            'workflow_step': 'confirm'
        }

    def _find_customer_by_name(self, customer_name: str, customers_df: pd.DataFrame) -> Optional[Dict]:
        """Find a customer by name with fuzzy matching"""
        customer_name_lower = customer_name.lower().strip()
        
        # Try exact match first
        exact_matches = customers_df[customers_df['customer_name'].str.lower().str.strip() == customer_name_lower]
        if len(exact_matches) > 0:
            return exact_matches.iloc[0].to_dict()
        
        # Try fuzzy matching if available
        if HAS_ENTITY_RESOLVER:
            best_match = None
            best_similarity = 0
            
            for _, customer in customers_df.iterrows():
                similarity = calculate_similarity(customer_name, customer['customer_name'])
                if similarity > best_similarity and similarity > 0.7:
                    best_similarity = similarity
                    best_match = customer.to_dict()
            
            if best_match:
                return best_match
        
        return None

    def _identify_remove_target(self, target_name: str, customers_df: pd.DataFrame) -> Dict:
        """Identify whether target is a customer or cluster"""
        target_name_lower = target_name.lower().strip()
        
        # Check if it's a customer name
        customer_match = self._find_customer_by_name(target_name, customers_df)
        if customer_match:
            return {
                'found': True,
                'type': 'customer',
                'customer_name': customer_match['customer_name'],
                'cluster': customer_match['cluster'],
                'reference': customer_match.get('reference 1', ''),
                'cache_path': customer_match.get('cache_path', '')
            }
        
        # Check if it's a cluster name
        cluster_matches = customers_df[customers_df['cluster'].str.lower().str.strip() == target_name_lower]
        if len(cluster_matches) > 0:
            customers_in_cluster = cluster_matches['customer_name'].tolist()
            return {
                'found': True,
                'type': 'cluster',
                'cluster': cluster_matches.iloc[0]['cluster'],
                'customers': customers_in_cluster,
                'count': len(customers_in_cluster)
            }
        
        return {'found': False}

    def _execute_add_customer(self, data: Dict, session_id: str) -> Dict:
        """Execute the add customer operation"""
        try:
            # Create cache path
            cache_path = self._sanitize_customer_name(data['customer_name'])
            
            # Backup existing CSV
            backup_path = f"{self.customers_file}.backup.{int(time.time())}"
            if os.path.exists(self.customers_file):
                shutil.copy2(self.customers_file, backup_path)
            
            # Load current data
            customers_df = self._load_customers_csv()
            
            # Create new row
            new_row = {
                'cluster': data['cluster'],
                'customer_name': data['customer_name'],
                'reference 1': data['reference_url'],
                'cache_path': cache_path
            }
            
            # Add to DataFrame
            new_df = pd.concat([customers_df, pd.DataFrame([new_row])], ignore_index=True)
            
            # Save to CSV
            new_df.to_csv(self.customers_file, index=False)
            
            # Create cache directory
            cache_dir = os.path.join(self.cache_directory, cache_path)
            os.makedirs(cache_dir, exist_ok=True)
            
            # Download reference content if cache manager is available
            if self.cache_manager:
                try:
                    # Use existing cache manager to download content
                    self.cache_manager._download_reference_to_cache(
                        data['reference_url'], 
                        cache_dir, 
                        data['customer_name']
                    )
                except Exception as e:
                    print(f"⚠️ Warning: Could not download reference content: {e}")
            
            # Clean up backup if everything succeeded
            if os.path.exists(backup_path):
                os.remove(backup_path)

            try:
                os.utime(self.customers_file)  # Touch file to update modification time
                print(f"🔄 Triggered customer data refresh after adding {data['customer_name']}")
            except Exception as e:
                print(f"⚠️ Warning: Could not trigger data refresh: {e}")

            # Offer analysis
            response = f"✅ **Customer Added Successfully!**\n\n"
            response += f"**Customer:** {data['customer_name']}\n"
            response += f"**Cluster:** {data['cluster']}\n"
            response += f"**Cache directory:** {cache_path}\n\n"
            response += "Would you like to run customer analysis now? (yes/no)"
            
            # Store analysis offer in pending confirmations for potential follow-up
            self.pending_confirmations[session_id] = {
                'workflow': 'analysis_offer',
                'step': 'confirm_analysis',
                'data': {'customer_name': data['customer_name']},
                'timestamp': time.time()
            }
            
            return {
                'success': True,
                'action': 'customer_added',
                'response': response,
                'customer_name': data['customer_name'],
                'cluster': data['cluster'],
                'cache_path': cache_path
            }
            
        except Exception as e:
            # Restore from backup if it exists
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, self.customers_file)
                os.remove(backup_path)
            
            return {
                'success': False,
                'action': 'add_failed',
                'response': f"❌ Failed to add customer: {str(e)}",
                'error': str(e)
            }

    def _execute_remove_operation(self, target_info: Dict, session_id: str) -> Dict:
        """Execute remove customer or cluster operation"""
        try:
            # Backup existing CSV
            backup_path = f"{self.customers_file}.backup.{int(time.time())}"
            shutil.copy2(self.customers_file, backup_path)
            
            # Load current data
            customers_df = self._load_customers_csv()
            
            if target_info['type'] == 'customer':
                # Remove single customer
                customer_name = target_info['customer_name']
                mask = customers_df['customer_name'].str.strip() != customer_name.strip()
                new_df = customers_df[mask]
                
                # Remove cache directory
                cache_path = target_info.get('cache_path', '')
                if cache_path:
                    cache_dir = os.path.join(self.cache_directory, cache_path)
                    if os.path.exists(cache_dir):
                        shutil.rmtree(cache_dir)
                
                response = f"✅ **Customer Removed**\n\n"
                response += f"**Removed:** {customer_name}\n"
                response += f"**From cluster:** {target_info['cluster']}\n"
                response += "**Cache data deleted**"
                
            else:  # cluster
                # Remove all customers in cluster
                cluster_name = target_info['cluster']
                customers_to_remove = target_info['customers']
                
                # Get cache paths before removal
                cluster_customers = customers_df[customers_df['cluster'].str.strip() == cluster_name.strip()]
                cache_paths = cluster_customers['cache_path'].dropna().tolist()
                
                # Remove from DataFrame
                mask = customers_df['cluster'].str.strip() != cluster_name.strip()
                new_df = customers_df[mask]
                
                # Remove cache directories
                for cache_path in cache_paths:
                    if cache_path and cache_path.strip():
                        cache_dir = os.path.join(self.cache_directory, cache_path.strip())
                        if os.path.exists(cache_dir):
                            shutil.rmtree(cache_dir)
                
                response = f"✅ **Cluster Removed**\n\n"
                response += f"**Removed cluster:** {cluster_name}\n"
                response += f"**Customers removed:** {len(customers_to_remove)}\n"
                for customer in customers_to_remove:
                    response += f"  • {customer}\n"
                response += "**All cache data deleted**"
            
            # Save updated CSV
            new_df.to_csv(self.customers_file, index=False)
            
            # Clean up backup
            os.remove(backup_path)
            
            return {
                'success': True,
                'action': 'removed',
                'response': response
            }
            
        except Exception as e:
            # Restore from backup
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, self.customers_file)
                os.remove(backup_path)
            
            return {
                'success': False,
                'action': 'remove_failed',
                'response': f"❌ Failed to remove: {str(e)}",
                'error': str(e)
            }

    def _execute_update_operation(self, data: Dict, session_id: str) -> Dict:
        """Execute update customer operation"""
        try:
            # Backup existing CSV
            backup_path = f"{self.customers_file}.backup.{int(time.time())}"
            shutil.copy2(self.customers_file, backup_path)
            
            # Load current data
            customers_df = self._load_customers_csv()
            customer_info = data['customer_info']
            customer_name = customer_info['customer_name']
            
            # Find customer row
            mask = customers_df['customer_name'].str.strip() == customer_name.strip()
            
            if data['update_type'] == 'cluster':
                # Update cluster
                new_cluster = data['new_value']
                customers_df.loc[mask, 'cluster'] = new_cluster
                
                response = f"✅ **Customer Updated**\n\n"
                response += f"**Customer:** {customer_name}\n"
                response += f"**Cluster changed to:** {new_cluster}"
                
            elif data['update_type'] == 'reference':
                # Update reference URL
                new_reference = data['new_value']
                customers_df.loc[mask, 'reference 1'] = new_reference
                
                # Handle cache based on user choice
                cache_action = data.get('cache_action', 'keep')
                cache_path = customer_info.get('cache_path', '')
                
                if cache_action == 'replace' and cache_path:
                    # Remove old cache and download new content
                    cache_dir = os.path.join(self.cache_directory, cache_path)
                    if os.path.exists(cache_dir):
                        shutil.rmtree(cache_dir)
                    os.makedirs(cache_dir, exist_ok=True)
                    
                    # Download new content if cache manager is available
                    if self.cache_manager:
                        try:
                            self.cache_manager._download_reference_to_cache(
                                new_reference, 
                                cache_dir, 
                                customer_name
                            )
                        except Exception as e:
                            print(f"⚠️ Warning: Could not download new reference content: {e}")
                
                response = f"✅ **Customer Updated**\n\n"
                response += f"**Customer:** {customer_name}\n"
                response += f"**Reference updated to:** {new_reference}\n"
                response += f"**Cache:** {cache_action}ed existing data"
            
            # Save updated CSV
            customers_df.to_csv(self.customers_file, index=False)
            
            # Clean up backup
            os.remove(backup_path)
            
            return {
                'success': True,
                'action': 'updated',
                'response': response
            }
            
        except Exception as e:
            # Restore from backup
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, self.customers_file)
                os.remove(backup_path)
            
            return {
                'success': False,
                'action': 'update_failed',
                'response': f"❌ Failed to update customer: {str(e)}",
                'error': str(e)
            }

    def _get_customer_help(self) -> Dict:
        """Get help text for customer management commands"""
        help_text = """📋 **Customer Management Help**

**View Customers:**
• "list customers" - Show all customers organized by cluster
• "show customers" - Same as above

**Add Customers:**
• "add customer" - Start guided workflow to add a new customer
• "create customer" - Same as above

**Remove Customers:**
• "remove customer" - Remove a specific customer
• "delete cluster" - Remove all customers in a cluster

**Update Customers:**
• "update customer" - Change customer cluster or reference URL
• "move customer" - Same as above

**Customer Data:**
• Each customer belongs to a cluster (team, department, etc.)
• Reference URL provides starting material for analysis
• Cache directories store downloaded content locally

**Workflow:**
All operations use guided workflows with confirmation steps to prevent accidents. You can type 'cancel' at any time to exit a workflow.

**Integration:**
After adding customers, you can run customer insights analysis to get detailed business intelligence reports."""

        return {
            'success': True,
            'action': 'help',
            'response': help_text
        }

    def cleanup_expired_sessions(self, timeout_seconds: int = 300) -> None:
        """Clean up expired workflow sessions (default: 5 minutes)"""
        current_time = time.time()
        expired_sessions = [
            session_id for session_id, timestamp in self.confirmation_timeouts.items()
            if current_time - timestamp > timeout_seconds
        ]

        for session_id in expired_sessions:
            self.pending_confirmations.pop(session_id, None)
            self.confirmation_timeouts.pop(session_id, None)

        if expired_sessions:
            print(f"🧹 Cleaned up {len(expired_sessions)} expired customer management sessions")


# =============================================================================
# API-COMPATIBLE FUNCTIONS FOR EXTERNAL USE
# =============================================================================

def create_customer_manager(data_directory: str = "data", cache_directory: str = "cache") -> CustomerManager:
    """Factory function to create a CustomerManager instance"""
    return CustomerManager(data_directory, cache_directory)


def process_customer_command(manager: CustomerManager, message: str, session_id: str = None) -> Dict:
    """
    Process a natural language customer management command
    
    Args:
        manager: CustomerManager instance
        message: User's natural language command
        session_id: Session ID for workflow tracking
    
    Returns:
        Dict with structure:
        {
            'success': bool,
            'action': str,  # 'list', 'add_workflow_started', 'customer_added', etc.
            'response': str,  # Human-readable response
            'workflow_step': str,  # Current workflow step (if applicable)
            'error': str  # Error message if failed
        }
    """
    if session_id is None:
        session_id = f"customer_{int(time.time())}"
    
    # First check if it's a customer command
    if not manager.is_customer_command(message):
        return {
            'success': False,
            'action': 'not_customer_command',
            'error': 'Message is not a customer management command',
            'response': "❓ This doesn't look like a customer management command. Try 'list customers', 'add customer', or 'help'."
        }
    
    # Process the command
    return manager.process_command_with_session(message, session_id)
