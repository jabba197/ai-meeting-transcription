document.addEventListener('DOMContentLoaded', function() {
    // Elements
    const uploadForm = document.getElementById('upload-form');
    const audioFileInput = document.getElementById('audio-file');
    const fileNameDisplay = document.getElementById('file-name');
    const submitButton = document.getElementById('submit-btn');
    const customPrompt = document.getElementById('custom-prompt'); 
    const resultsSection = document.getElementById('results-section');
    const summaryText = document.getElementById('summary-text');
    const transcriptionText = document.getElementById('transcription-text'); // Add reference for transcript display
    const tabButtons = document.querySelectorAll('.tab-btn');
    const summaryTabButton = document.querySelector('.tab-btn[data-tab="summary"]');
    const tabPanes = document.querySelectorAll('.tab-pane');
    const loadingIndicator = document.getElementById('loading');
    const progressBar = document.getElementById('progress-bar'); 
    const progressStatus = document.getElementById('progress-status'); 
    const businessContext = document.getElementById('business-context');
    const savedCustomInstructions = document.getElementById('saved-custom-instructions');
    const saveContextButton = document.getElementById('save-context-btn');
    const copySummaryButton = document.getElementById('copy-summary-btn'); 
    const ragStatusIndicator = document.getElementById('rag-status-indicator'); // Get status indicator element
    const promptDetailsContainer = document.getElementById('prompt-details-container'); // New element
    const systemPromptText = document.getElementById('system-prompt-text'); // New element
    const userMessageText = document.getElementById('user-message-text'); // New element

    // --- Context Loading --- 
    function loadContext() {
        fetch('/get_context') 
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                if (data) {
                    businessContext.value = data.business_context || '';
                    savedCustomInstructions.value = data.custom_instructions || '';
                    // Update RAG status indicator
                    updateRagStatusIndicator(data.rag_status || 'unknown'); 
                } else {
                     console.warn('Could not load context or context is empty.');
                }
            })
            .catch(error => {
                console.error('Error loading context:', error);
                // Update status to unknown or error state on failure
                updateRagStatusIndicator('unknown'); 
                alert('Could not load saved context settings. ' + error.message);
            });
    }

    // Load context when the page is ready
    loadContext();

    // File selection handler
    audioFileInput.addEventListener('change', function() {
        if (this.files && this.files[0]) {
            fileNameDisplay.textContent = this.files[0].name;
            submitButton.disabled = false;
        } else {
            fileNameDisplay.textContent = 'Choose File';
            submitButton.disabled = true;
        }
    });

    // Tab switching functionality
    tabButtons.forEach(button => {
        button.addEventListener('click', () => {
            // Only proceed if the button is not already active
            if (!button.classList.contains('active')) {
                 // Remove active class from all buttons and panes
                tabButtons.forEach(btn => btn.classList.remove('active'));
                tabPanes.forEach(pane => pane.classList.remove('active'));

                // Add active class to clicked button and corresponding pane
                button.classList.add('active');
                const tabName = button.getAttribute('data-tab');
                const targetPane = document.getElementById(`${tabName}-content`);
                if(targetPane) {
                    targetPane.classList.add('active');
                } else {
                    console.error(`Tab pane for '${tabName}' not found.`);
                }
            }
        });
    });

    // Save context functionality
    saveContextButton.addEventListener('click', function() {
        const contextData = {
            business_context: businessContext.value,
            custom_instructions: savedCustomInstructions.value 
        };

        // Show some indicator that saving is happening (optional)
        saveContextButton.textContent = 'Saving...';
        saveContextButton.disabled = true;

        fetch('/save_context', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(contextData)
        })
        .then(response => response.json())
        .then(data => {
            if (data.message) { 
                alert(data.message);
            } else if (data.error) { 
                alert('Failed to save context settings: ' + data.error);
            } else {
                 alert('Failed to save context settings. Please try again.');
            }
        })
        .catch(error => {
            console.error('Error saving context:', error);
            alert('An error occurred while saving the context settings.');
        })
        .finally(() => {
             // Restore button text and state
             saveContextButton.textContent = 'Save Context Settings';
             saveContextButton.disabled = false;
        });
    });

    // --- Copy Summary Button ---  
    copySummaryButton.addEventListener('click', function() {
        const summaryContent = summaryText.innerText; // Get the plain text content
        navigator.clipboard.writeText(summaryContent)
            .then(() => {
                // Provide feedback to the user
                copySummaryButton.textContent = 'Copied!';
                copySummaryButton.disabled = true;
                setTimeout(() => {
                    copySummaryButton.textContent = 'Copy';
                    copySummaryButton.disabled = false;
                }, 2000); // Reset after 2 seconds
            })
            .catch(err => {
                console.error('Failed to copy text: ', err);
                alert('Failed to copy summary. Please try again or copy manually.');
            });
    });

    // Helper function to update progress
    function updateProgress(percentage, status) {
        if (progressBar && progressStatus) {
            progressBar.style.width = percentage + '%';
            progressStatus.textContent = status;
        } else {
            console.warn('Progress bar or status element not found.');
        }
    }

    // Form submission handler
    uploadForm.addEventListener('submit', function(e) {
        e.preventDefault();

        if (!audioFileInput.files || !audioFileInput.files[0]) {
            alert('Please select an audio file first.');
            return;
        }

        // Show loading indicator and reset progress
        loadingIndicator.classList.remove('hidden');
        updateProgress(0, 'Starting...'); 
        submitButton.disabled = true; 
        resultsSection.classList.add('hidden'); 
        summaryText.innerHTML = ''; 
        transcriptionText.innerHTML = ''; // Also clear transcription text
        promptDetailsContainer.classList.add('hidden'); // Hide prompt details initially

        // Create FormData for upload
        const uploadFormData = new FormData();
        uploadFormData.append('file', audioFileInput.files[0]); // Ensure key matches Flask ('file')
        uploadFormData.append('user_prompt', customPrompt.value); // Add user prompt

        updateProgress(10, 'Uploading audio...');

        // Step 1: Upload the file
        fetch('/upload', {
            method: 'POST',
            body: uploadFormData // Send FormData directly
        })
        .then(response => {
             if (!response.ok) {
                 // Try to parse error JSON, otherwise throw generic HTTP error
                 return response.json().then(err => {
                     throw new Error(err.error || `Upload failed: ${response.status}`);
                 }).catch(() => {
                     throw new Error(`Upload failed: ${response.status} ${response.statusText}`);
                 });
             }
             return response.json(); // Parse the JSON response from /upload
        })
        .then(uploadData => {
            if (!uploadData.filepath) {
                throw new Error('Upload succeeded but filepath was missing in response.');
            }
            updateProgress(50, 'Audio uploaded. Starting transcription & summarization...');

            // Step 2: Call /transcribe with the filepath and user prompt
            return fetch('/transcribe', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    filepath: uploadData.filepath,
                    filename: uploadData.filename, // Pass filename for saving summary
                    prompt: customPrompt.value // Get prompt from textarea
                })
            });
        })
        .then(transcribeResponse => {
            if (!transcribeResponse.ok) {
                // Try to parse error JSON, otherwise throw generic HTTP error
                return transcribeResponse.json().then(err => {
                    throw new Error(err.error || `Processing failed: ${transcribeResponse.status}`);
                }).catch(() => {
                    throw new Error(`Processing failed: ${transcribeResponse.status} ${transcribeResponse.statusText}`);
                });
            }
            return transcribeResponse.json(); // Parse JSON response from /transcribe
        })
        .then(data => {
            // Processing complete, display results
            updateProgress(100, 'Processing complete.');
            loadingIndicator.classList.add('hidden');

            // Check for application-level errors returned in the JSON (e.g., transcription blocked)
            if (data.error) {
                // Display error in summary area or dedicated error div
                summaryText.innerHTML = `<p class="error">Error: ${data.error}</p>`;
                transcriptionText.innerHTML = ''; // Clear transcription on error
                promptDetailsContainer.classList.add('hidden'); // Keep prompts hidden on error
                console.error('Processing error:', data.error);
            } else {
                // Display successful results
                // Use textContent for security, or sanitize if HTML is needed
                summaryText.textContent = data.summary || 'No summary generated.';
                transcriptionText.textContent = data.transcription || 'No transcription available.';

                // Populate and show prompt details
                systemPromptText.textContent = data.system_prompt || 'System prompt not available.';
                userMessageText.textContent = data.user_message || 'User message not available.';
                promptDetailsContainer.classList.remove('hidden'); // Show the details dropdown

                // Activate the summary tab by default
                activateTab('summary');
            }

            // Show the results section regardless of success/error
            resultsSection.classList.remove('hidden');
            submitButton.disabled = false; // Re-enable button

        })
        .catch(error => {
            // Handle network errors or errors thrown from .then blocks
            console.error('Error during processing:', error);
            loadingIndicator.classList.add('hidden');
            resultsSection.classList.remove('hidden'); // Show results section to display error
            summaryText.innerHTML = `<p class="error">An error occurred: ${error.message}. Please check the console or server logs.</p>`;
            transcriptionText.innerHTML = '';
            promptDetailsContainer.classList.add('hidden');
            submitButton.disabled = false; // Re-enable button
        });
    });

    // Helper function to activate a specific tab
    function activateTab(tabName) {
        tabButtons.forEach(btn => btn.classList.remove('active'));
        tabPanes.forEach(pane => pane.classList.remove('active'));
        
        const targetButton = document.querySelector(`.tab-btn[data-tab="${tabName}"]`);
        const targetPane = document.getElementById(`${tabName}-content`);
        
        if(targetButton) targetButton.classList.add('active');
        if(targetPane) targetPane.classList.add('active');
    }

    // --- RAG Status Indicator --- 
    function updateRagStatusIndicator(status) {
        if (!ragStatusIndicator) return; // Guard clause if element not found

        ragStatusIndicator.classList.remove('status-green', 'status-amber', 'status-red', 'status-unknown');
        let statusText = '';
        let statusTitle = 'RAG Database Status: ';

        switch (status) {
            case 'green':
                ragStatusIndicator.classList.add('status-green');
                statusText = '●'; // Green dot
                statusTitle += 'Ready';
                break;
            case 'amber':
                ragStatusIndicator.classList.add('status-amber');
                statusText = '●'; // Amber dot
                statusTitle += 'Initializing / Not Configured';
                break;
            case 'red':
                ragStatusIndicator.classList.add('status-red');
                statusText = '●'; // Red dot
                statusTitle += 'Error';
                break;
            default: // unknown or other
                ragStatusIndicator.classList.add('status-unknown');
                statusText = '?'; // Question mark
                statusTitle += 'Unknown';
                break;
        }
        ragStatusIndicator.textContent = statusText;
        ragStatusIndicator.title = statusTitle;
    }

    // Re-enable submit button after successful context load (or initial state)
    // This might need adjustment based on when context is truly needed
    submitButton.disabled = false;

}); // End of DOMContentLoaded
