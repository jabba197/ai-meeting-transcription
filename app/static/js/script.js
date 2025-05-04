document.addEventListener('DOMContentLoaded', function() {
    // Elements
    const uploadForm = document.getElementById('upload-form');
    const audioFileInput = document.getElementById('audio-file');
    const fileNameDisplay = document.getElementById('file-name');
    const submitButton = document.getElementById('submit-btn');
    const customPrompt = document.getElementById('custom-prompt'); 
    const resultsSection = document.getElementById('results-section');
    const summaryText = document.getElementById('summary-text');
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
                } else {
                     console.warn('Could not load context or context is empty.');
                }
            })
            .catch(error => {
                console.error('Error loading context:', error);
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

        // Create FormData and append both the file and the user prompt
        const formData = new FormData();
        formData.append('file', audioFileInput.files[0]); // Ensure key matches Flask ('file')
        formData.append('user_prompt', customPrompt.value); // Add user prompt

        updateProgress(10, 'Uploading and processing audio...');

        // Single fetch call to the updated /upload endpoint
        fetch('/upload', {
            method: 'POST',
            body: formData // Send FormData directly
        })
        .then(response => {
             // Check if the response is OK (status 200-299)
             if (!response.ok) {
                 // Try to parse the error JSON, otherwise throw generic HTTP error
                 return response.json().then(err => { 
                     throw new Error(err.error || `Server error: ${response.status}`);
                 }).catch(() => { // Handle cases where response is not JSON (e.g., HTML error page)
                     throw new Error(`Server error: ${response.status} ${response.statusText}`);
                 });
             }
             return response.json(); // Parse the JSON response from /upload
        })
        .then(data => {
            // This block now receives the final summary data directly
            updateProgress(95, 'Processing complete.');
            // Hide loading indicator
            loadingIndicator.classList.add('hidden');

            // Check for application-level errors returned in the JSON
            if (data.error) {
                throw new Error(data.error);
            }

            // Display the summary
            resultsSection.classList.remove('hidden');
            if (typeof marked === 'undefined') {
                console.error('marked.js library not found. Markdown rendering disabled.');
                summaryText.textContent = data.summary || 'No summary was generated.';
            } else {
                 try {
                     // Sanitize potentially harmful HTML before parsing Markdown
                     // Basic sanitization example (consider a more robust library like DOMPurify if needed)
                     const sanitizedMarkdown = (data.summary || 'No summary was generated.').replace(/<script.*?>.*?<\/script>/gi, '');
                     summaryText.innerHTML = marked.parse(sanitizedMarkdown);
                 } catch (markdownError) {
                     console.error('Error parsing Markdown:', markdownError);
                     summaryText.textContent = data.summary || 'No summary was generated (Markdown parsing failed).';
                 }
            }

            // Update progress to 100%
             updateProgress(100, 'Done!');
        })
        .catch(error => {
            console.error('Error during upload/summarization:', error);
            // Hide loading indicator
            loadingIndicator.classList.add('hidden');
            // Show error message prominently
            summaryText.innerHTML = `<p class="error-message"><strong>Error:</strong> ${error.message || 'An unknown error occurred.'}</p>`;
            resultsSection.classList.remove('hidden');
            // Reset progress bar on error
             updateProgress(0, 'Error');
        })
        .finally(() => {
             // Re-enable the submit button regardless of success or failure
             submitButton.disabled = false; 
             // Clear the file input for the next upload
             // audioFileInput.value = ''; // Optional: uncomment to clear file input after processing
             // fileNameDisplay.textContent = 'Choose File'; // Optional: uncomment to reset file name display
        });
    });

});
