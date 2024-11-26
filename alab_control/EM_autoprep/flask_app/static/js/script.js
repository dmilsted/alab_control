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

