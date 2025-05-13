function updateSEMDestinationAndHighlight(selectedValue) {
    // Update hidden destination field
    document.getElementById("destination").value = selectedValue;

    // Reset all circle colors to white
    const circles = document.querySelectorAll("#svgContainer circle");
    circles.forEach(circle => circle.setAttribute("fill", "white"));

    // Highlight selected circle
    const selectedCircle = document.getElementById(selectedValue);
    if (selectedCircle) {
        selectedCircle.setAttribute("fill", "lightgreen");
    }
}

function updateTEMDestinationAndHighlight(selectedValue) {
    let targetGroup;
    let highlightColor;

    if (selectedValue.startsWith("TC")) {
        targetGroup = "[id^='TC']";
        highlightColor = "lightgreen";
    } else if (selectedValue.startsWith("TE")) {
        targetGroup = "[id^='TE']";
        highlightColor = "lightblue";
    } else {
        return; // Do nothing if the selected value doesn't start with TC or TE
    }

    // Reset all squares in the target group
    const allSquares = document.querySelectorAll(targetGroup);
    allSquares.forEach(square => square.setAttribute('fill', 'white'));

    // Highlight the selected square
    const selectedSquare = document.getElementById(selectedValue);
    if (selectedSquare) {
        selectedSquare.setAttribute('fill', highlightColor);
    }
}

function updateSEMStageOriginAndHighlight(selectedValue) {
    // Reset all SEM tray circles to white
    const semCircles = document.querySelectorAll("#semTrayContainer circle");
    semCircles.forEach(circle => circle.setAttribute("fill", "white"));

    // Highlight selected SEM tray circle
    const selectedSemCircle = document.getElementById(selectedValue);
    if (selectedSemCircle) {
        selectedSemCircle.setAttribute("fill", "lightgreen");
    }
}

function updateSEMStageDestinationAndHighlight(selectedValue) {
    // Extract the actual position value from the PH_STUB_X format
    const positionValue = selectedValue.replace('PH_STUB_', '').toLowerCase();
    
    // Remove 'selected' class from all circles
    const allCircles = document.querySelectorAll("#stageContainer circle");
    allCircles.forEach(circle => {
        circle.classList.remove("selected");
    });

    // Find the corresponding circle using stg_X format
    const circleId = `stg_${positionValue}`;
    const selectedElement = document.getElementById(circleId);
    
    // Check if selected value is a forbidden position
    if (selectedElement && selectedElement.classList.contains('forbidden-position')) {
        alert("This position is not available for selection.");
        return;
    }

    // Add 'selected' class to the selected circle
    if (selectedElement) {
        selectedElement.classList.add("selected");
    } else {
        console.error(`Could not find element with ID: ${circleId}`);
    }
}

/*
function updateSEMDestinationAndHighlight(selectedValue) {
    // Update hidden destination field
    document.getElementById("destination").value = selectedValue;

    // Reset all circle colors to white
    const circles = document.querySelectorAll("#svgContainer circle");
    circles.forEach(circle => circle.setAttribute("fill", "white"));

    // Highlight selected circle
    const selectedCircle = document.getElementById(selectedValue);
    if (selectedCircle) {
        selectedCircle.setAttribute("fill", "lightgreen");
    }
}

function updateTEMDestinationAndHighlight(selectedValue) {
    let targetGroup;
    let highlightColor;

    if (selectedValue.startsWith("TC")) {
        targetGroup = "[id^='TC']";
        highlightColor = "lightgreen";
    } else if (selectedValue.startsWith("TE")) {
        targetGroup = "[id^='TE']";
        highlightColor = "lightblue";
    } else {
        return; // Do nothing if the selected value doesn't start with TC or TE
    }

    // Reset all squares in the target group
    const allSquares = document.querySelectorAll(targetGroup);
    allSquares.forEach(square => square.setAttribute('fill', 'white'));

    // Highlight the selected square
    const selectedSquare = document.getElementById(selectedValue);
    if (selectedSquare) {
        selectedSquare.setAttribute('fill', highlightColor);
    }
}

function updateSEMStageOriginAndHighlight(selectedValue) {
    // Reset all SEM tray circles to white
    const semCircles = document.querySelectorAll("#semTrayContainer circle");
    semCircles.forEach(circle => circle.setAttribute("fill", "white"));

    // Highlight selected SEM tray circle
    const selectedSemCircle = document.getElementById(selectedValue);
    if (selectedSemCircle) {
        selectedSemCircle.setAttribute("fill", "lightgreen");
    }
}

function updateSEMStageDestinationAndHighlight(selectedValue) {
    // Remove 'selected' class from all circles
    const allCircles = document.querySelectorAll("#stageContainer circle");
    allCircles.forEach(circle => {
        // Only remove the 'selected' class, preserving the base class
        circle.classList.remove("selected");
    });

    // Check if selected value is a forbidden position
    const selectedElement = document.getElementById(selectedValue);
    if (selectedElement && selectedElement.classList.contains('forbidden-position')) {
        alert("This position is not available for selection.");
        return;
    }

    // Add 'selected' class to the selected circle
    if (selectedElement) {
        selectedElement.classList.add("selected");
        // The circle still maintains its 'selectable-position' class
    }
} */