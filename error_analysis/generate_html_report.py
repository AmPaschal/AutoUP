import json
import html
from typing import Dict, Any, List
from datetime import datetime

# 95% of this code was generated using Claude 4

class HTMLTestReportGenerator:
    def __init__(self):
        self.html_content = []
    
    def generate_css(self) -> str:
        """Generate CSS styles for the report."""
        return """
        <style>
            * {
                box-sizing: border-box;
            }
            
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                line-height: 1.6;
                margin: 0;
                padding: 20px;
                background-color: #f8f9fa;
                color: #333;
            }
            
            .container {
                max-width: 1200px;
                margin: 0 auto;
                background: white;
                border-radius: 8px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                padding: 30px;
            }
            
            h1 {
                color: #2c3e50;
                border-bottom: 3px solid #3498db;
                padding-bottom: 10px;
                margin-bottom: 30px;
            }
            
            h2 {
                color: #34495e;
                margin-top: 30px;
                margin-bottom: 20px;
                border-left: 4px solid #3498db;
                padding-left: 15px;
            }
            
            h3 {
                color: #5a6c7d;
                margin-top: 25px;
                margin-bottom: 15px;
            }
            
            .summary-section {
                margin-bottom: 40px;
            }
            
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                border-radius: 6px;
                overflow: hidden;
            }
            
            th, td {
                padding: 12px 15px;
                text-align: left;
                border-bottom: 1px solid #ddd;
            }
            
            th {
                background-color: #3498db;
                color: white;
                font-weight: 600;
            }
            
            tr:nth-child(even) {
                background-color: #f8f9fa;
            }
            
            tr:hover {
                background-color: #e8f4f8;
            }
            
            details {
                margin: 20px 0;
                border: 1px solid #ddd;
                border-radius: 6px;
                overflow: hidden;
            }
            
            summary {
                background-color: #f1f3f4;
                padding: 15px;
                cursor: pointer;
                font-weight: 600;
                border-bottom: 1px solid #ddd;
                transition: background-color 0.2s;
            }
            
            summary:hover {
                background-color: #e8eaed;
            }
            
            summary::marker {
                color: #3498db;
            }
            
            .details-content {
                padding: 20px;
            }
            
            .harness-section {
                margin: 30px 0;
                border: 2px solid #3498db;
                border-radius: 8px;
                overflow: hidden;
            }
            
            .harness-section > summary {
                background-color: #3498db;
                color: white;
                font-size: 1.1em;
                font-weight: bold;
            }
            
            .harness-section > summary:hover {
                background-color: #2980b9;
            }
            
            .error-section {
                margin: 15px 0;
                border: 1px solid #e74c3c;
                border-radius: 6px;
            }
            
            .error-section > summary {
                background-color: #fee;
                color: #c0392b;
                border-bottom: 1px solid #e74c3c;
            }
            
            .error-section > summary:hover {
                background-color: #fdd;
            }
            
            pre {
                background-color: #f8f9fa;
                border: 1px solid #e9ecef;
                border-radius: 4px;
                padding: 15px;
                overflow-x: auto;
                font-size: 14px;
                line-height: 1.4;
                margin: 10px 0;
            }
            
            code {
                background-color: #f8f9fa;
                padding: 2px 6px;
                border-radius: 3px;
                font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
                font-size: 0.9em;
            }
            
            .status-success {
                color: #27ae60;
                font-weight: bold;
            }
            
            .status-failed {
                color: #e74c3c;
                font-weight: bold;
            }
            
            .metric-highlight {
                background-color: #e8f5e8;
                padding: 3px 8px;
                border-radius: 4px;
                font-weight: bold;
            }
            
            .timestamp {
                color: #7f8c8d;
                font-size: 0.9em;
                text-align: right;
                margin-top: 30px;
                padding-top: 20px;
                border-top: 1px solid #eee;
            }
            
            .nested-details {
                margin: 10px 0;
                border: 1px solid #bdc3c7;
            }
            
            .nested-details > summary {
                background-color: #ecf0f1;
                color: #2c3e50;
                font-size: 0.95em;
            }
            
            .json-content {
                background-color: #2c3e50;
                color: #ecf0f1;
                border-radius: 4px;
                padding: 15px;
                overflow-x: auto;
                font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
                font-size: 13px;
                line-height: 1.4;
            }
            
            .search-container {
                margin-bottom: 20px;
                text-align: right;
            }
            
            .search-box {
                padding: 8px 12px;
                border: 1px solid #ddd;
                border-radius: 4px;
                font-size: 14px;
                width: 250px;
            }
        </style>
        """
    
    def generate_javascript(self) -> str:
        """Generate JavaScript for interactive features."""
        return """
        <script>
            function searchReport() {
                const searchTerm = document.getElementById('searchBox').value.toLowerCase();
                const details = document.querySelectorAll('details');
                
                details.forEach(detail => {
                    const content = detail.textContent.toLowerCase();
                    if (content.includes(searchTerm) || searchTerm === '') {
                        detail.style.display = '';
                        if (searchTerm !== '') {
                            detail.open = true;
                        }
                    } else {
                        detail.style.display = 'none';
                    }
                });
            }
            
            function expandAll() {
                document.querySelectorAll('details').forEach(detail => {
                    detail.open = true;
                });
            }
            
            function collapseAll() {
                document.querySelectorAll('details').forEach(detail => {
                    detail.open = false;
                });
            }
            
            // Syntax highlighting for JSON
            function highlightJSON() {
                document.querySelectorAll('.json-content').forEach(element => {
                    let content = element.textContent;
                    try {
                        const parsed = JSON.parse(content);
                        element.innerHTML = JSON.stringify(parsed, null, 2);
                    } catch (e) {
                        // Keep original content if not valid JSON
                    }
                });
            }
            
            document.addEventListener('DOMContentLoaded', function() {
                highlightJSON();
            });
        </script>
        """
    
    def dict_to_table(self, data: Dict[str, Any], title: str = "") -> str:
        """Convert dictionary to HTML table."""
        if not data:
            return ""
        
        # Handle nested dictionaries by flattening them
        flattened = {}
        for key, value in data.items():
            if isinstance(value, dict):
                for subkey, subvalue in value.items():
                    flattened[f"{key} - {subkey}"] = subvalue
            else:
                flattened[key] = value
        
        table_html = []
        if title:
            table_html.append(f"<h3>{html.escape(title)}</h3>")
        
        table_html.append("<table>")
        table_html.append("<thead><tr><th>Metric</th><th>Value</th></tr></thead>")
        table_html.append("<tbody>")
        
        for key, value in flattened.items():
            # Add special styling for certain values
            value_str = str(value)
            if key.lower().endswith('success') and str(value).lower() == 'true':
                value_str = f'<span class="status-success">{value}</span>'
            elif key.lower().endswith('failed') and str(value).lower() == 'true':
                value_str = f'<span class="status-failed">{value}</span>'
            elif 'resolved' in key.lower() and title != "Error Summary":
                value_str = f'<span class="metric-highlight">{value}</span>'
            
            table_html.append(f"<tr><td>{html.escape(key)}</td><td>{value_str}</td></tr>")
        
        table_html.append("</tbody></table>")
        return "".join(table_html)
    
    def format_code_block(self, content: Any, css_class: str = "") -> str:
        """Format content as a code block."""
        if isinstance(content, dict):
            content_str = json.dumps(content, indent=2)
        elif isinstance(content, list) and all(isinstance(item, str) for item in content):
            content_str = '\n'.join(content)
        else:
            content_str = str(content)
        
        css_class = f' class="{css_class}"' if css_class else ''
        return f'<pre{css_class}>{html.escape(content_str)}</pre>'
    
    def create_details_section(self, title: str, content: str, open_by_default: bool = True, css_class: str = "") -> str:
        """Create HTML details/summary collapsible section."""
        open_attr = "open" if open_by_default else ""
        class_attr = f' class="{css_class}"' if css_class else ''
        
        return f"""
        <details {open_attr}{class_attr}>
            <summary>{html.escape(title)}</summary>
            <div class="details-content">
                {content}
            </div>
        </details>
        """
    
    def process_harness_data(self, harness: Dict[str, Any]) -> str:
        """Process individual harness data and return HTML content."""
        content = []
        
        # Basic information table
        basic_info = {
            'Success': harness.get('Success', 'Error'),
            'Errors Resolved (%)': harness.get('% Of Errors Resolved', 'N/A'),
            'Initial Error Count': harness.get('Initial # of Errors', 'N/A'),
            'Execution Time (s)': round(harness.get('Execution Time', 0), 2) if harness.get('Execution Time') else 'N/A'
        }
        content.append(self.dict_to_table(basic_info, "Basic Information"))
        
        if "Error" in harness:
            content.append(self.create_details_section(harness["Error"], 
                                                       harness["Traceback"], False, "error-section"))

        # Preconditions sections
        if 'Preconditions Removed' in harness and harness['Preconditions Removed']:
            precond_content = self.format_code_block(harness['Preconditions Removed'])
            content.append(self.create_details_section("Preconditions Removed", precond_content, True, "nested-details"))
        
        if 'Preconditions Added' in harness and harness['Preconditions Added']:
            precond_content = self.format_code_block(harness['Preconditions Added'])
            content.append(self.create_details_section("Preconditions Added", precond_content, True, "nested-details"))
        
        # Summary and token usage tables
        if 'Summary' in harness:
            content.append(self.dict_to_table(harness['Summary'], "Error Summary"))
        
        if 'Total Token Usage' in harness:
            content.append(self.dict_to_table(harness['Total Token Usage'], "Token Usage"))
        
        # Initial Errors section
        if 'Initial Errors' in harness and harness['Initial Errors']:
            initial_errors_html = []
            for error_type, errors in harness['Initial Errors'].items():
                initial_errors_html.append(f"<h4>{html.escape(error_type)}</h4>")
                initial_errors_html.append(self.format_code_block(errors))
            
            initial_errors_content = "".join(initial_errors_html)
            content.append(self.create_details_section("Initial Errors", initial_errors_content, False, "nested-details"))
        
        # Processed Errors section
        if len(harness['Successful Errors']) > 0:
            processed_errors_content = self.process_processed_errors(harness['Successful Errors'], mode='success')
            content.append(self.create_details_section("Successful Errors", processed_errors_content, False, "nested-details"))
        
        if len(harness['Failed Errors']) > 0:
            processed_errors_content = self.process_processed_errors(harness['Failed Errors'], mode='failed')
            content.append(self.create_details_section("Failed Errors", processed_errors_content, False, "nested-details"))

        return "".join(content)
    
    def process_processed_errors(self, processed_errors: List[Dict[str, Any]], mode) -> str:
        """Process the processed errors section."""
        content = []
        
        for i, error in enumerate(processed_errors, 1):
            error_content = []
            
            # Error details table
            error_info = {
                'Error': error.get('Error', 'N/A'),
                'Attempts': error.get('Attempts', 'N/A'),
                'Resolved': error.get('Resolved', 'N/A')
            }
            if mode == 'failed' and 'Resolved By' in error:
                error_info['Resolved By'] = error['Resolved By'][:100]

            error_content.append(self.dict_to_table(error_info, f"Error {i} Details"))
            
            # Preconditions Added
            if 'Preconditions Added' in error:
                precond_content = self.format_code_block(error['Preconditions Added'])
                error_content.append(f"<h4>Preconditions Added</h4>{precond_content}")
            
            # Token Usage
            if 'Token Usage' in error:
                error_content.append(self.dict_to_table(error['Token Usage'], "Token Usage"))
            
            # Indirectly Resolved
            if len(error['Indirectly Resolved']) > 0:
                indirect_content = self.format_code_block(error['Indirectly Resolved'])
                error_content.append(self.create_details_section("Indirectly Resolved Errors", indirect_content, False, "nested-details"))
            
            # Raw Responses
            if 'Raw Responses' in error and error['Raw Responses']:
                responses_html = []
                for j, response in enumerate(error['Raw Responses'], 1):
                    responses_html.append(f"<h5>Response {j}{f" ({response['reason_for_failure']})" if response.get('reason_for_failure', None) != None else ""}</h5>")

                    responses_html.append(self.format_code_block(response, "json-content"))
                
                responses_content = "".join(responses_html)
                error_content.append(self.create_details_section("Raw Responses", responses_content, False, "nested-details"))
            
            # Create collapsible section for this error
            error_section_content = "".join(error_content)
            title_str = f"Error {i}: {error.get('Error', 'Unknown')[:100]}..."
            if mode == 'failed' and not error.get('Resolved', True):
                title_str += " (UNRESOLVED)"
            error_section = self.create_details_section(title_str, error_section_content, False, "error-section")
            content.append(error_section)
        
        return "".join(content)
    
    def generate_report(self, data: Dict[str, Any], output_filename: str = "test_report.html") -> str:
        """Generate HTML report from test run data."""
        
        html_parts = []
        
        # HTML head
        html_parts.append("""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Test Run Report</title>
        """)
        
        html_parts.append(self.generate_css())
        html_parts.append("</head><body>")
        
        # Container start
        html_parts.append('<div class="container">')
        
        # Title and controls
        html_parts.append('<h1>🧪 Test Run Report</h1>')
        
        # Search and control buttons
        html_parts.append("""
        <div class="search-container">
            <input type="text" id="searchBox" class="search-box" placeholder="Search report..." onkeyup="searchReport()">
            <button onclick="expandAll()" style="margin-left: 10px; padding: 8px 12px;">Expand All</button>
            <button onclick="collapseAll()" style="padding: 8px 12px;">Collapse All</button>
        </div>
        """)
        
        # html_parts.append('<h2>Results Summary<h2>')

        # Overall Summary section
        if 'Summary' in data:
            html_parts.append('<div class="summary-section">')
            html_parts.append(self.dict_to_table(data['Summary']['Harnesses'], "Harnesses Fixed"))
            html_parts.append(self.dict_to_table(data['Summary']['Errors'], "Errors Resolved"))
            html_parts.append('</div>')
        
        # Overall Token Usage section
        if 'Total Token Usage' in data:
            html_parts.append('<div class="summary-section">')
            html_parts.append(self.dict_to_table(data['Total Token Usage'], "Total Token Usage"))
            html_parts.append('</div>')
        
        # Process each harness
        if 'Harnesses' in data and data['Harnesses']:
            for harness in data['Harnesses']:
                harness_name = harness.get('Harness', 'Unknown Harness')
                harness_content = self.process_harness_data(harness)
                
                # Create collapsible section for entire harness
                harness_section = self.create_details_section(f"{harness_name}{" (FAILED)" if not harness.get('Success', False) else ""}", harness_content, False, "harness-section")
                html_parts.append(harness_section)
        
        # Timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        html_parts.append(f'<div class="timestamp">Report generated on {timestamp}</div>')
        
        # Container end and JavaScript
        html_parts.append('</div>')
        html_parts.append(self.generate_javascript())
        html_parts.append('</body></html>')
        
        # Write to file
        html_content = "".join(html_parts)

        with open("./results/index.html", 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"HTML report generated successfully: {output_filename}")
        return output_filename

def generate_html_report(data: Dict[str, Any], output_filename: str = "test_report.html") -> str:
    """Convenience function to generate HTML report."""
    generator = HTMLTestReportGenerator()
    return generator.generate_report(data, output_filename)